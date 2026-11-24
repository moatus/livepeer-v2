// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IUpgrades} from "../../interfaces/IUpgrades.sol";
import {IProxy} from "../../interfaces/IProxy.sol";
import {IRoundClock} from "../../interfaces/IRoundClock.sol";

/// The admin of every upgradeable contract's proxy, the upgrade key U, the
/// pause key S and the two stops (rulebook R-27). Fixed: it has no admin of
/// its own, and U is never replaced.
///
/// An announcement is applicable from its round + 7 and lapses at its round
/// + 28; while its contract's side is stopped it may be applied before its
/// notice ends, never after it lapses. A stop set in round P is in force
/// through P + 6. A pause key named in round n counts from n + 7. Every one
/// of these is read against the round when asked, so nothing ends by an
/// event.
contract Upgrades is IUpgrades {
    uint256 private constant NOTICE = 7;
    uint256 private constant LAPSE = 28;
    uint256 private constant STOP = 7;
    uint256 private constant SUCCESSION = 7;

    struct Announcement {
        address code;
        bytes data;
        uint256 appliesFrom;
        uint256 lapsesAt;
    }

    /// The round clock, read through its proxy.
    address public immutable clock;
    address public immutable upgrader;

    /// The pause key before any naming counts; address 0 once removed.
    address private pauserKey;
    /// A naming, kept until another naming or a removal: in force from
    /// namedFrom.
    address private named;
    uint256 private namedFrom;

    /// side => the round its stop ends at, as last set; 0 if never set or
    /// lifted. Indexed by Side.USDC and Side.LPT.
    uint256[2] private endsAt;

    /// proxy => its side + 1; 0 for an address that is not one of the proxies.
    mapping(address => uint256) private sides;
    mapping(address => Announcement) private announced;

    /// `proxies[i]` is on `sides_[i]`. Refuses U or S zero or equal with
    /// KEY_INVALID.
    constructor(address clock_, address upgrader_, address pauser_, address[] memory proxies, Side[] memory sides_) {
        if (upgrader_ == address(0) || pauser_ == address(0) || upgrader_ == pauser_) revert KEY_INVALID();
        require(clock_ != address(0) && proxies.length == sides_.length, "clock and one side per proxy");
        clock = clock_;
        upgrader = upgrader_;
        pauserKey = pauser_;
        for (uint256 i = 0; i < proxies.length; i++) {
            require(sides[proxies[i]] == 0, "a proxy named twice");
            sides[proxies[i]] = uint256(sides_[i]) + 1;
        }
    }

    // --- the upgrade key's calls ---------------------------------------------

    /// Refusals in R-27's order: NOT_AUTHORIZED, NOT_UPGRADEABLE,
    /// CODE_INVALID. Replaces any announcement pending for the proxy.
    function announceUpgrade(address proxy, address code, bytes calldata data) external returns (uint256 appliesFrom) {
        if (msg.sender != upgrader) revert NOT_AUTHORIZED();
        _sideOf(proxy);
        if (code.code.length == 0) revert CODE_INVALID(code);
        uint256 now_ = _round();
        appliesFrom = now_ + NOTICE;
        announced[proxy] = Announcement(code, data, appliesFrom, now_ + LAPSE);
        emit UpgradeAnnounced(proxy, code, data, appliesFrom, now_ + LAPSE);
    }

    /// Refusals in R-27's order: NOT_AUTHORIZED, NOT_UPGRADEABLE,
    /// NOTHING_PENDING, UPGRADE_LAPSED, UPGRADE_NOT_READY, then the proxy's
    /// CODE_INVALID and the initialisation call's own, its bytes unchanged.
    /// The announcement is cleared before the proxy is called, and a refusal
    /// reverts the clearing with the rest.
    function applyUpgrade(address proxy) external {
        if (msg.sender != upgrader) revert NOT_AUTHORIZED();
        Side side = _sideOf(proxy);
        Announcement memory a = announced[proxy];
        if (a.code == address(0)) revert NOTHING_PENDING();
        uint256 now_ = _round();
        if (now_ >= a.lapsesAt) revert UPGRADE_LAPSED(a.lapsesAt);
        bool underStop = now_ < _stopEndsAt(side);
        if (now_ < a.appliesFrom && !underStop) revert UPGRADE_NOT_READY(a.appliesFrom);
        delete announced[proxy];
        // A refusal reverts this call with the proxy's bytes unchanged.
        IProxy(proxy).upgradeToAndCall(a.code, a.data);
        emit UpgradeApplied(proxy, a.code, now_, underStop);
    }

    /// Refusals: NOT_AUTHORIZED, NOT_UPGRADEABLE, NOTHING_PENDING. A lapsed
    /// announcement may be withdrawn.
    function withdrawUpgrade(address proxy) external {
        if (msg.sender != upgrader) revert NOT_AUTHORIZED();
        _sideOf(proxy);
        address code = announced[proxy].code;
        if (code == address(0)) revert NOTHING_PENDING();
        delete announced[proxy];
        emit UpgradeWithdrawn(proxy, code);
    }

    /// One side, refused NOT_STOPPED when it is not in force; BOTH lifts
    /// each side in force and is refused only when neither is.
    function liftStop(Side side) external {
        if (msg.sender != upgrader) revert NOT_AUTHORIZED();
        uint256 now_ = _round();
        bool lifted = false;
        for (uint256 s = 0; s < 2; s++) {
            if ((side == Side.BOTH || uint256(side) == s) && now_ < endsAt[s]) {
                endsAt[s] = 0;
                lifted = true;
                emit StopLifted(Side(s), now_);
            }
        }
        if (!lifted) revert NOT_STOPPED();
    }

    /// At once; also cancels a pending naming.
    function removePauser() external {
        if (msg.sender != upgrader) revert NOT_AUTHORIZED();
        uint256 now_ = _round();
        address was = _pauser(now_);
        (pauserKey, named, namedFrom) = (address(0), address(0), 0);
        emit PauserRemoved(was, now_);
    }

    /// `next` counts from 7 rounds on; until then the key in force keeps its
    /// power. Replaces a naming not yet in force. Refusals: NOT_AUTHORIZED,
    /// then KEY_INVALID for zero or U.
    function namePauser(address next) external returns (uint256 countsFrom) {
        if (msg.sender != upgrader) revert NOT_AUTHORIZED();
        if (next == address(0) || next == upgrader) revert KEY_INVALID();
        uint256 now_ = _round();
        pauserKey = _pauser(now_);
        countsFrom = now_ + SUCCESSION;
        (named, namedFrom) = (next, countsFrom);
        emit PauserNamed(next, countsFrom);
    }

    // --- the pause key's call ------------------------------------------------

    /// In force through the round 6 rounds on; setting a side again while in
    /// force restarts its 7 rounds. One StopSet per side.
    function setStop(Side side) external returns (uint256 endsAt_) {
        uint256 now_ = _round();
        address key = _pauser(now_);
        if (msg.sender != key || key == address(0)) revert NOT_AUTHORIZED();
        endsAt_ = now_ + STOP;
        for (uint256 s = 0; s < 2; s++) {
            if (side == Side.BOTH || uint256(side) == s) {
                endsAt[s] = endsAt_;
                emit StopSet(Side(s), now_, endsAt_);
            }
        }
    }

    // --- reads -----------------------------------------------------------------

    /// One storage read, and a clock read only while a stop is recorded.
    function stopped(Side side) external view returns (bool) {
        uint256 e = _stopEndsAt(side);
        return e != 0 && _round() < e;
    }

    function stopEndsAt(Side side) external view returns (uint256) {
        return _stopEndsAt(side);
    }

    function pending(address proxy)
        external
        view
        returns (address code, bytes memory data, uint256 appliesFrom, uint256 lapsesAt)
    {
        _sideOf(proxy);
        Announcement storage a = announced[proxy];
        return (a.code, a.data, a.appliesFrom, a.lapsesAt);
    }

    function sideOf(address proxy) external view returns (Side) {
        return _sideOf(proxy);
    }

    function pauser() external view returns (address) {
        return _pauser(_round());
    }

    function pauserNamed() external view returns (address next, uint256 countsFrom) {
        if (named == address(0) || _round() >= namedFrom) return (address(0), 0);
        return (named, namedFrom);
    }

    // --- internals -------------------------------------------------------------

    function _round() private view returns (uint256) {
        return IRoundClock(clock).currentRound();
    }

    function _sideOf(address proxy) private view returns (Side) {
        uint256 s = sides[proxy];
        if (s == 0) revert NOT_UPGRADEABLE(proxy);
        return Side(s - 1);
    }

    /// BOTH's end is the lower of the two, so BOTH is stopped while both
    /// sides are.
    function _stopEndsAt(Side side) private view returns (uint256) {
        if (side != Side.BOTH) return endsAt[uint256(side)];
        return endsAt[0] < endsAt[1] ? endsAt[0] : endsAt[1];
    }

    /// The named key from its round on, else the key before it.
    function _pauser(uint256 now_) private view returns (address) {
        return named != address(0) && now_ >= namedFrom ? named : pauserKey;
    }
}
