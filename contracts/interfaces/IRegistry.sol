// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// The registry of nodes and their operator keys (rulebook october-main-text-v3): who is
/// a node and where its USDC half is paid (R-01), and each node's one
/// operator key (R-26), the node itself until a proposed key accepts. Only
/// that key acts for the node.
interface IRegistry {
    /// Anyone, at any time, free. Registration is permanent; registering
    /// again changes nothing and is not refused.
    function register() external;

    /// Sets `node`'s payout address. address(0) makes it the node itself
    /// again. Refused with NOT_AUTHORIZED unless the caller is the node's
    /// operator key, then NOT_REGISTERED when `node` is not a node, and
    /// PAYOUT_INVALID when `payout` is the Broker's address.
    function setPayout(address node, address payout) external;

    /// Proposes `next` as `node`'s operator key, replacing any proposal;
    /// proposing the current operator key clears it. Refused with
    /// NOT_AUTHORIZED unless the caller is the operator key, then
    /// NOT_REGISTERED, then OPERATOR_INVALID when `next` is address(0).
    function setOperator(address node, address next) external;

    /// The proposed key takes over, in this call, and the proposal slot
    /// empties. Refused with NOT_AUTHORIZED for any caller but the key in
    /// the node's slot, or when the slot is empty.
    function acceptOperator(address node) external;

    function isRegistered(address a) external view returns (bool);

    /// The same as isRegistered: a registered node with zero stake is active.
    /// It does not mean the validator set.
    function isActiveTranscoder(address a) external view returns (bool);

    /// The node itself until its operator key sets another.
    function payoutOf(address node) external view returns (address);

    /// The node itself until a proposed key accepts.
    function operatorOf(address node) external view returns (address);

    /// address(0) when none is proposed.
    function proposedOperatorOf(address node) external view returns (address);

    /// Whether `caller` may act for `node`: caller == operatorOf(node). It
    /// reads nothing else about the node; whether `node` is registered is
    /// checked after it.
    function mayActFor(address caller, address node) external view returns (bool);

    event NodeRegistered(address indexed node, uint256 round);
    event PayoutSet(address indexed node, address payout);
    event OperatorProposed(address indexed node, address indexed next);
    event OperatorSet(address indexed node, address indexed operator);

    error NOT_REGISTERED();
    error PAYOUT_INVALID();
    error NOT_AUTHORIZED();
    error OPERATOR_INVALID();
}
