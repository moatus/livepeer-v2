// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {ITicketBroker} from "../../interfaces/ITicketBroker.sol";
import {IRoundClock} from "../../interfaces/IRoundClock.sol";
import {IMockUSDC} from "../../interfaces/IMockUSDC.sol";
import {IRegistry} from "../../interfaces/IRegistry.sol";

/// Holds all USDC: payer accounts, used tickets, fees per round and node,
/// and the network share (rulebook R-03, R-04, R-21, R-22). The node finds
/// it under the name "TicketBroker" and calls it with the current
/// protocol's names, shapes and ticket hash.
///
/// Stage 2, phase 2: funding, redemption and the node's reads. Release
/// (requestRelease, release) is built in phase 4; until then nothing is
/// ever pending, so every read below shows the whole balance, and this
/// contract does not yet declare the whole ITicketBroker shape.
contract Broker {
    /// Node cut (R-03): 500,000 over 10^6.
    uint256 private constant NODE_CUT = 500_000;
    uint256 private constant ONE = 1_000_000;
    /// Ticket validity (R-22): the creation round and the 2 rounds after it.
    uint256 private constant TICKET_VALIDITY = 2;
    /// Half the secp256k1 order: a larger s is the malleable twin of a
    /// signature and is refused, so a submitter cannot re-roll the win test.
    uint256 private constant HALF_N = 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0;

    IMockUSDC private immutable usdc;
    IRegistry private immutable registry;
    IRoundClock private immutable clock;

    mapping(address => uint256) private deposit;
    mapping(address => mapping(address => uint256)) private earmark;
    /// Each payer's earmarks summed, kept on every change so that no read
    /// visits the nodes a payer has named (R-23).
    mapping(address => uint256) private earmarkTotal;

    mapping(bytes32 => bool) public usedTickets;
    mapping(uint256 => mapping(address => uint256)) public feesOf;
    mapping(uint256 => uint256) public totalFees;
    uint256 public networkHeld;

    constructor(IMockUSDC usdc_, IRegistry registry_, IRoundClock clock_) {
        usdc = usdc_;
        registry = registry_;
        clock = clock_;
    }

    // --- payer ---------------------------------------------------------

    /// Takes `deposit_ + earmark_` of the caller's USDC (R-21). With
    /// earmark 0 it funds the deposit and names no node.
    function fund(uint256 deposit_, address node, uint256 earmark_) external {
        if (earmark_ == 0) {
            node = address(0);
        } else {
            if (!registry.isRegistered(node)) revert ITicketBroker.NOT_REGISTERED();
            earmark[msg.sender][node] += earmark_;
            earmarkTotal[msg.sender] += earmark_;
        }
        deposit[msg.sender] += deposit_;
        emit ITicketBroker.Funded(msg.sender, deposit_, node, earmark_);
        require(usdc.transferFrom(msg.sender, address(this), deposit_ + earmark_), "USDC transfer failed");
    }

    // --- what the node is shown ---------------------------------------

    /// node address(0) is the deposit. Nothing is pending before phase 4.
    function earmarkOf(address payer, address node)
        external
        view
        returns (uint256 amount, uint256 pending, uint256 readyRound)
    {
        amount = node == address(0) ? deposit[payer] : earmark[payer][node];
        return (amount, 0, 0);
    }

    /// The deposit, and every earmark summed as fundsRemaining (R-21).
    /// withdrawRound and claimedInCurrentRound are always 0.
    function getSenderInfo(address payer)
        external
        view
        returns (ITicketBroker.Sender memory, ITicketBroker.ReserveInfo memory)
    {
        return (ITicketBroker.Sender(deposit[payer], 0), ITicketBroker.ReserveInfo(earmarkTotal[payer], 0));
    }

    /// That node's earmark: the per-node guarantee the node checks against
    /// the face before it accepts a ticket (R-21).
    function claimableReserve(address payer, address node) external view returns (uint256) {
        return earmark[payer][node];
    }

    // --- redemption -----------------------------------------------------

    /// Anyone may submit a ticket; the node's half goes only to the
    /// recipient's payout address (R-03). Pays min(face, deposit + that
    /// node's earmark), deposit first (R-04).
    function redeemWinningTicket(ITicketBroker.Ticket calldata t, bytes calldata sig, uint256 recipientRand)
        external
    {
        bytes32 ticketHash = keccak256(
            abi.encodePacked(
                t.recipient, t.sender, t.faceValue, t.winProb, t.senderNonce, t.recipientRandHash, t.auxData
            )
        );
        uint256 round = _requireValid(t, ticketHash, sig, recipientRand);
        if (!registry.isRegistered(t.recipient)) revert ITicketBroker.NOT_REGISTERED();

        uint256 fromDeposit = deposit[t.sender];
        uint256 fromEarmark = earmark[t.sender][t.recipient];
        if (fromDeposit >= t.faceValue) {
            (fromDeposit, fromEarmark) = (t.faceValue, 0);
        } else if (fromDeposit + fromEarmark > t.faceValue) {
            fromEarmark = t.faceValue - fromDeposit;
        }
        uint256 paid = fromDeposit + fromEarmark;
        if (paid == 0) revert ITicketBroker.NOTHING_TO_PAY();

        usedTickets[ticketHash] = true;
        deposit[t.sender] -= fromDeposit;
        earmark[t.sender][t.recipient] -= fromEarmark;
        earmarkTotal[t.sender] -= fromEarmark;

        uint256 nodeAmount = paid * NODE_CUT / ONE;
        uint256 networkAmount = paid - nodeAmount;
        networkHeld += networkAmount;
        feesOf[round][t.recipient] += paid;
        totalFees[round] += paid;

        address payout = registry.payoutOf(t.recipient);
        emit ITicketBroker.FeeSettled(
            round, t.recipient, t.sender, ticketHash, paid, nodeAmount, networkAmount, payout
        );
        if (nodeAmount > 0) require(usdc.transfer(payout, nodeAmount), "USDC transfer failed");
    }

    /// Today's checks, in today's order. Returns the current round.
    function _requireValid(
        ITicketBroker.Ticket calldata t,
        bytes32 ticketHash,
        bytes calldata sig,
        uint256 recipientRand
    ) private view returns (uint256 round) {
        if (t.recipient == address(0) || t.sender == address(0)) revert ITicketBroker.TICKET_INVALID();
        if (t.auxData.length != 64) revert ITicketBroker.TICKET_INVALID();
        (uint256 creationRound, bytes32 creationHash) = abi.decode(t.auxData, (uint256, bytes32));
        bytes32 roundHash = clock.blockHashForRound(creationRound);
        // Kept as text: the node treats this revert as final.
        require(roundHash != bytes32(0), "ticket creationRound does not have a block hash");
        if (creationHash != roundHash) revert ITicketBroker.TICKET_INVALID();
        round = clock.currentRound();
        if (round > creationRound + TICKET_VALIDITY) revert ITicketBroker.TICKET_EXPIRED();
        if (keccak256(abi.encodePacked(recipientRand)) != t.recipientRandHash) {
            revert ITicketBroker.TICKET_INVALID();
        }
        if (usedTickets[ticketHash]) revert ITicketBroker.TICKET_USED();
        if (_signer(ticketHash, sig) != t.sender) revert ITicketBroker.TICKET_INVALID();
        if (uint256(keccak256(abi.encodePacked(sig, recipientRand))) >= t.winProb) {
            revert ITicketBroker.TICKET_INVALID();
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
