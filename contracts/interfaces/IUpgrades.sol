// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// The admin of every upgradeable contract's proxy, the upgrade key, the
/// pause key and the two stops (rulebook R-27). Fixed at deployment.
interface IUpgrades {
    /// A contract's side: USDC (the Broker), LPT (StakeLedger, Claims,
    /// Scores, the Buyback) or BOTH (Parameters, Registry, RoundClock).
    /// For setStop and liftStop, BOTH means each of the two; stopped(BOTH)
    /// is stopped(USDC) && stopped(LPT).
    enum Side { USDC, LPT, BOTH }

    function announceUpgrade(address proxy, address code, bytes calldata data)
        external returns (uint256 appliesFrom);
    function applyUpgrade(address proxy) external;
    function withdrawUpgrade(address proxy) external;
    function liftStop(Side side) external;
    function removePauser() external;
    function namePauser(address next) external returns (uint256 countsFrom);
    function setStop(Side side) external returns (uint256 endsAt);

    function stopped(Side side) external view returns (bool);
    /// The round the side's stop ends at as last set, kept after it ends;
    /// 0 if never set or lifted. A side is stopped while the current round
    /// is below it. BOTH: the lower of the two.
    function stopEndsAt(Side side) external view returns (uint256);
    /// The announcement held for a proxy, kept after it lapses (lapsesAt
    /// at or below the current round shows it); (0, "", 0, 0) when none.
    /// Refuses NOT_UPGRADEABLE for an address that is not one of its proxies.
    function pending(address proxy) external view
        returns (address code, bytes memory data, uint256 appliesFrom, uint256 lapsesAt);
    function sideOf(address proxy) external view returns (Side);
    function upgrader() external view returns (address);
    /// The pause key in force this round; address 0 when removed.
    function pauser() external view returns (address);
    /// A naming not yet in force; (0, 0) when none, and from its countsFrom
    /// round on, when the named key is pauser().
    function pauserNamed() external view returns (address next, uint256 countsFrom);
    function clock() external view returns (address);

    event UpgradeAnnounced(address indexed proxy, address code, bytes data, uint256 appliesFrom, uint256 lapsesAt);
    event UpgradeWithdrawn(address indexed proxy, address code);
    event UpgradeApplied(address indexed proxy, address code, uint256 round, bool underStop);
    /// One per side. A StopSet while that side's stop is in force is a renewal.
    /// A stop lapses at endsAt with no event: RoundStarted(endsAt) marks it.
    event StopSet(Side indexed side, uint256 round, uint256 endsAt);
    event StopLifted(Side indexed side, uint256 round);
    /// Also cancels a pending naming.
    event PauserRemoved(address indexed pauser, uint256 round);
    event PauserNamed(address indexed next, uint256 countsFrom);

    error NOT_AUTHORIZED();
    error NOT_UPGRADEABLE(address proxy);
    error CODE_INVALID(address code);
    error NOTHING_PENDING();
    error UPGRADE_NOT_READY(uint256 appliesFrom);
    error UPGRADE_LAPSED(uint256 lapsesAt);
    error NOT_STOPPED();
    error KEY_INVALID();
}
