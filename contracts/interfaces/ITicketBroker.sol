// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// Holds all USDC: payer accounts, used tickets, fees per round and node,
/// and the network share (rulebook R-03, R-04, R-21, R-22). The node finds
/// it under the name "TicketBroker" and calls it with the current
/// protocol's names, shapes and ticket hash.
interface ITicketBroker {
    struct Ticket {
        address recipient;
        address sender;
        uint256 faceValue;
        uint256 winProb;
        uint256 senderNonce;
        bytes32 recipientRandHash;
        bytes auxData;
    }

    struct Sender {
        uint256 deposit;
        uint256 withdrawRound;
    }

    struct ReserveInfo {
        uint256 fundsRemaining;
        uint256 claimedInCurrentRound;
    }

    /// Takes `deposit + earmark` of the caller's USDC. With earmark 0 it funds
    /// the deposit and names no node. Refused with NOT_REGISTERED when an
    /// earmark names an address that is not a registered node.
    function fund(uint256 deposit, address node, uint256 earmark) external;

    /// node address(0) is the deposit. At most the balance, or refused with
    /// AMOUNT_TOO_LARGE. Replaces a pending request and restarts its wait;
    /// amount 0 cancels.
    function requestRelease(address node, uint256 amount) external;

    /// From the request's round + 3. Refused with RELEASE_NOT_READY when
    /// none is pending or before its round.
    function release(address node) external;

    /// node address(0) is the deposit, as in requestRelease.
    function earmarkOf(address payer, address node)
        external
        view
        returns (uint256 amount, uint256 pending, uint256 readyRound);

    /// Sender.deposit is the deposit less its pending release, never below 0;
    /// withdrawRound is 0. ReserveInfo.fundsRemaining is the sum of each
    /// earmark less its pending release, each never below 0;
    /// claimedInCurrentRound is 0.
    function getSenderInfo(address payer) external view returns (Sender memory, ReserveInfo memory);

    /// That node's earmark less its pending release, never below 0.
    function claimableReserve(address payer, address node) external view returns (uint256);

    function usedTickets(bytes32 ticketHash) external view returns (bool);

    /// Anyone may submit a ticket; the node's half goes only to the
    /// recipient's payout address. Pays min(face, deposit + that node's
    /// earmark), deposit first.
    function redeemWinningTicket(Ticket calldata t, bytes calldata sig, uint256 recipientRand) external;

    function feesOf(uint256 round, address node) external view returns (uint256);

    function totalFees(uint256 round) external view returns (uint256);

    function networkHeld() external view returns (uint256);

    event Funded(address indexed payer, uint256 deposit, address indexed node, uint256 earmark);
    event ReleaseRequested(address indexed payer, address indexed node, uint256 amount, uint256 readyRound);
    event Released(address indexed payer, address indexed node, uint256 amount);
    event FeeSettled(
        uint256 indexed round,
        address indexed node,
        address indexed payer,
        bytes32 ticketHash,
        uint256 gross,
        uint256 nodeAmount,
        uint256 networkAmount,
        address payout
    );

    error NOT_REGISTERED();
    error TICKET_EXPIRED();
    error TICKET_USED();
    error TICKET_INVALID();
    error NOTHING_TO_PAY();
    error AMOUNT_TOO_LARGE();
    error RELEASE_NOT_READY();
    // And the kept revert text "ticket creationRound does not have a block
    // hash", for a creation round not started, which the node treats as final.
}

/// The Broker as stage 4 reads it (rulebook october-main-text-v4, R-16):
/// ITicketBroker and the Buyback's draw of the network share. The Broker's
/// constructor takes the Buyback's predicted address.
interface ITicketBrokerV2 is ITicketBroker {
    /// The Buyback, fixed at deployment.
    function buyback() external view returns (address);

    /// Only the Buyback: pays `amount` of the network share to `to` and
    /// lowers networkHeld by it. Refused with NOT_AUTHORIZED from any other
    /// address, then AMOUNT_TOO_LARGE above networkHeld.
    function drawNetwork(uint256 amount, address to) external;

    event NetworkDrawn(address indexed to, uint256 amount);

    error NOT_AUTHORIZED();
}
