// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {INetworkShareTicketBroker} from "../../interfaces/ITicketBroker.sol";
import {IRoundClock} from "../../interfaces/IRoundClock.sol";
import {IMockUSDC} from "../../interfaces/IMockUSDC.sol";
import {IRegistry} from "../../interfaces/IRegistry.sol";
import {IParameters} from "../../interfaces/IParameters.sol";

/// Holds all USDC: payer accounts, used tickets, fees per round and node,
/// and the network share (rulebook R-03, R-04, R-21, R-22). The node finds
/// it under the name "TicketBroker" and calls it with the current
/// protocol's names, shapes and ticket hash. The network share leaves it
/// only through drawNetwork, to the executor group 10 of the Parameters
/// names for the round the draw runs in (R-16, R-25).
contract Broker is INetworkShareTicketBroker {
    /// The fee group (R-25): the node cut over 10^6 is its first value.
    uint256 private constant FEE = 1;
    /// The executor group (R-25): the one address that may draw the network
    /// share in a round (R-16 rule 10).
    uint256 private constant BUYBACK_EXECUTOR = 10;
    uint256 private constant ONE = 1_000_000;
    /// Ticket validity (R-22): the creation round and the 2 rounds after it.
    uint256 private constant TICKET_VALIDITY = 2;
    /// Release wait (R-22): a request may be released from its round + 3.
    uint256 private constant RELEASE_WAIT = 3;
    /// Half the secp256k1 order: a larger s is the malleable twin of a
    /// signature and is refused, so a submitter cannot re-roll the win test.
    uint256 private constant HALF_N = 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0;

    IMockUSDC private immutable usdc;
    IRegistry private immutable registry;
    IRoundClock private immutable clock;
    /// Where the node cut is read, for the round a fee is redeemed in, and
    /// the executor, for the round a draw runs in (R-03, R-16, R-25).
    IParameters private immutable params;

    /// One balance of a payer: its deposit (node address(0)) or its earmark
    /// for one node, with the release pending on it (R-22). A pending
    /// amount is never above the balance, and a cleared request has
    /// pending and readyRound 0.
    struct Balance {
        uint256 amount;
        uint256 pending;
        uint256 readyRound;
    }

    mapping(address => mapping(address => Balance)) private balance;
    /// Each payer's earmarks less their pending releases, summed, kept on
    /// every change so that no read visits the nodes a payer has named
    /// (R-23).
    mapping(address => uint256) private shownEarmarks;

    mapping(bytes32 => bool) public usedTickets;
    mapping(uint256 => mapping(address => uint256)) public feesOf;
    mapping(uint256 => uint256) public totalFees;
    uint256 public networkHeld;

    constructor(IMockUSDC usdc_, IRegistry registry_, IRoundClock clock_, IParameters params_) {
        require(address(params_) != address(0), "zero address");
        usdc = usdc_;
        registry = registry_;
        clock = clock_;
        params = params_;
    }

    // --- payer ---------------------------------------------------------

    /// Takes `deposit_ + earmark_` of the caller's USDC (R-21). With
    /// earmark 0 it funds the deposit and names no node. Funding leaves a
    /// pending request as it is (R-22).
    function fund(uint256 deposit_, address node, uint256 earmark_) external {
        if (earmark_ == 0) {
            node = address(0);
        } else {
            if (!registry.isRegistered(node)) revert NOT_REGISTERED();
            Balance storage e = balance[msg.sender][node];
            _set(msg.sender, node, e, e.amount + earmark_, e.pending, e.readyRound);
        }
        Balance storage d = balance[msg.sender][address(0)];
        _set(msg.sender, address(0), d, d.amount + deposit_, d.pending, d.readyRound);
        emit Funded(msg.sender, deposit_, node, earmark_);
        require(usdc.transferFrom(msg.sender, address(this), deposit_ + earmark_), "USDC transfer failed");
    }

    /// Asks to take `amount` out of the deposit (node address(0)) or one
    /// earmark, at most its balance (R-22). From now the node is shown the
    /// balance less `amount`. A new request replaces the pending one and
    /// restarts the wait; amount 0 cancels it.
    function requestRelease(address node, uint256 amount) external {
        Balance storage b = balance[msg.sender][node];
        if (amount > b.amount) revert AMOUNT_TOO_LARGE();
        uint256 readyRound = amount == 0 ? 0 : clock.currentRound() + RELEASE_WAIT;
        _set(msg.sender, node, b, b.amount, amount, readyRound);
        emit ReleaseRequested(msg.sender, node, amount, readyRound);
    }

    /// Pays the payer its pending amount from the request's round + 3, and
    /// clears the request (R-22).
    function release(address node) external {
        Balance storage b = balance[msg.sender][node];
        uint256 amount = b.pending;
        if (amount == 0 || clock.currentRound() < b.readyRound) revert RELEASE_NOT_READY();
        _set(msg.sender, node, b, b.amount - amount, 0, 0);
        emit Released(msg.sender, node, amount);
        require(usdc.transfer(msg.sender, amount), "USDC transfer failed");
    }

    // --- what the node is shown ---------------------------------------

    /// node address(0) is the deposit, as in requestRelease.
    function earmarkOf(address payer, address node)
        external
        view
        returns (uint256 amount, uint256 pending, uint256 readyRound)
    {
        Balance storage b = balance[payer][node];
        return (b.amount, b.pending, b.readyRound);
    }

    /// The deposit less its pending release, and every earmark less its
    /// pending release, summed as fundsRemaining (R-21, R-22). Neither can
    /// fall below 0, since nothing pending is above its balance.
    /// withdrawRound and claimedInCurrentRound are always 0.
    function getSenderInfo(address payer) external view returns (Sender memory, ReserveInfo memory) {
        Balance storage d = balance[payer][address(0)];
        return (Sender(d.amount - d.pending, 0), ReserveInfo(shownEarmarks[payer], 0));
    }

    /// That node's earmark less its pending release: the per-node guarantee
    /// the node checks against the face before it accepts a ticket (R-21).
    function claimableReserve(address payer, address node) external view returns (uint256) {
        if (node == address(0)) return 0;
        Balance storage e = balance[payer][node];
        return e.amount - e.pending;
    }

    // --- redemption -----------------------------------------------------

    /// Anyone may submit a ticket; the node's half goes only to the
    /// recipient's payout address (R-03). Pays min(face, deposit + that
    /// node's earmark), deposit first (R-04), drawing on the whole balance
    /// whatever is pending.
    function redeemWinningTicket(Ticket calldata t, bytes calldata sig, uint256 recipientRand) external {
        bytes32 ticketHash = keccak256(
            abi.encodePacked(
                t.recipient, t.sender, t.faceValue, t.winProb, t.senderNonce, t.recipientRandHash, t.auxData
            )
        );
        uint256 round = _requireValid(t, ticketHash, sig, recipientRand);
        if (!registry.isRegistered(t.recipient)) revert NOT_REGISTERED();

        Balance storage d = balance[t.sender][address(0)];
        Balance storage e = balance[t.sender][t.recipient];
        uint256 fromDeposit = d.amount;
        uint256 fromEarmark = e.amount;
        if (fromDeposit >= t.faceValue) {
            (fromDeposit, fromEarmark) = (t.faceValue, 0);
        } else if (fromDeposit + fromEarmark > t.faceValue) {
            fromEarmark = t.faceValue - fromDeposit;
        }
        uint256 paid = fromDeposit + fromEarmark;
        if (paid == 0) revert NOTHING_TO_PAY();

        usedTickets[ticketHash] = true;
        _draw(t.sender, address(0), d, fromDeposit);
        _draw(t.sender, t.recipient, e, fromEarmark);

        uint256 nodeAmount = _nodeAmount(paid, round);
        uint256 networkAmount = paid - nodeAmount;
        networkHeld += networkAmount;
        feesOf[round][t.recipient] += paid;
        totalFees[round] += paid;

        address payout = registry.payoutOf(t.recipient);
        emit FeeSettled(round, t.recipient, t.sender, ticketHash, paid, nodeAmount, networkAmount, payout);
        if (nodeAmount > 0) require(usdc.transfer(payout, nodeAmount), "USDC transfer failed");
    }

    // --- the network share --------------------------------------------

    /// Only the executor in force for the current round, group 10: pays
    /// `amount` of the network share to `to`, the address it names (R-16
    /// rule 10). Refused with NOT_AUTHORIZED from any other caller, and from
    /// every caller while group 10 is address(0), then AMOUNT_TOO_LARGE
    /// above networkHeld.
    function drawNetwork(uint256 amount, address to) external {
        address executor = address(uint160(params.valuesAt(BUYBACK_EXECUTOR, clock.currentRound())[0]));
        if (msg.sender != executor || executor == address(0)) revert NOT_AUTHORIZED();
        if (amount > networkHeld) revert AMOUNT_TOO_LARGE();
        networkHeld -= amount;
        emit NetworkDrawn(to, amount);
        require(usdc.transfer(to, amount), "USDC transfer failed");
    }

    // --- internals ------------------------------------------------------

    /// floor(paid x cut / 10^6), with the node cut in force in `round`, the
    /// round the fee is redeemed in (R-03, R-25), split so that no product
    /// overflows for any amount a payer can fund.
    function _nodeAmount(uint256 paid, uint256 round) private view returns (uint256) {
        uint256 cut = params.valuesAt(FEE, round)[0];
        return paid / ONE * cut + paid % ONE * cut / ONE;
    }

    /// Lowers a balance by what a ticket took, and its pending amount to
    /// the balance left where it is above it; a pending amount that
    /// reaches 0 is cleared, so a later top-up is shown at once (R-22).
    function _draw(address payer, address node, Balance storage b, uint256 taken) private {
        uint256 left = b.amount - taken;
        if (b.pending < left) {
            _set(payer, node, b, left, b.pending, b.readyRound);
        } else {
            _set(payer, node, b, left, left, left == 0 ? 0 : b.readyRound);
        }
    }

    /// The one place a balance changes, so the earmarks shown to the signer
    /// stay summed (R-23).
    function _set(address payer, address node, Balance storage b, uint256 amount, uint256 pending, uint256 readyRound)
        private
    {
        if (node != address(0)) {
            shownEarmarks[payer] = shownEarmarks[payer] - (b.amount - b.pending) + (amount - pending);
        }
        (b.amount, b.pending, b.readyRound) = (amount, pending, readyRound);
    }

    /// Today's checks, in today's order. Returns the current round.
    function _requireValid(Ticket calldata t, bytes32 ticketHash, bytes calldata sig, uint256 recipientRand)
        private
        view
        returns (uint256 round)
    {
        if (t.recipient == address(0) || t.sender == address(0)) revert TICKET_INVALID();
        if (t.auxData.length != 64) revert TICKET_INVALID();
        (uint256 creationRound, bytes32 creationHash) = abi.decode(t.auxData, (uint256, bytes32));
        bytes32 roundHash = clock.blockHashForRound(creationRound);
        // Kept as text: the node treats this revert as final.
        require(roundHash != bytes32(0), "ticket creationRound does not have a block hash");
        if (creationHash != roundHash) revert TICKET_INVALID();
        round = clock.currentRound();
        if (round > creationRound + TICKET_VALIDITY) revert TICKET_EXPIRED();
        if (keccak256(abi.encodePacked(recipientRand)) != t.recipientRandHash) {
            revert TICKET_INVALID();
        }
        if (usedTickets[ticketHash]) revert TICKET_USED();
        if (_signer(ticketHash, sig) != t.sender) revert TICKET_INVALID();
        if (uint256(keccak256(abi.encodePacked(sig, recipientRand))) >= t.winProb) {
            revert TICKET_INVALID();
        }
    }

    /// The Ethereum signed-message signer of `hash`, with v 27 or 28 and a
    /// low s; address(0) for any other signature.
    function _signer(bytes32 hash, bytes calldata sig) private pure returns (address) {
        if (sig.length != 65) return address(0);
        bytes32 r = bytes32(sig[0:32]);
        bytes32 s = bytes32(sig[32:64]);
        uint8 v = uint8(sig[64]);
        if ((v != 27 && v != 28) || uint256(s) > HALF_N) return address(0);
        return ecrecover(keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", hash)), v, r, s);
    }
}
