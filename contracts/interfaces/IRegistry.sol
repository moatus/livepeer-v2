// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// Who is a node, and where its USDC half is paid (rulebook R-01). The node
/// finds it under the name "BondingManager".
interface IRegistry {
    /// Anyone, at any time, free. Registration is permanent; registering
    /// again changes nothing and is not refused.
    function register() external;

    /// Sets the caller's own payout address. address(0) makes it the node
    /// itself again. Refused with NOT_REGISTERED when the caller is not a
    /// node, and with PAYOUT_INVALID when `payout` is the Broker's address.
    function setPayout(address payout) external;

    function isRegistered(address a) external view returns (bool);

    /// The same as isRegistered: a registered node with zero stake is active.
    /// It does not mean the validator set.
    function isActiveTranscoder(address a) external view returns (bool);

    /// The node itself until it sets another.
    function payoutOf(address node) external view returns (address);

    event NodeRegistered(address indexed node, uint256 round);
    event PayoutSet(address indexed node, address payout);

    error NOT_REGISTERED();
    error PAYOUT_INVALID();
}

/// The Registry as stage 3 reads it (rulebook october-main-text-v2):
/// IRegistry and one view, the one authority check. It is declared beside
/// IRegistry because the stage 2 Registry, which implements IRegistry, does
/// not have it; S-05's phase 3 builds it into the Registry. StakeLedger,
/// Claims and Scores read isRegistered, payoutOf and mayActFor at execution.
interface IRegistryV2 is IRegistry {
    /// Whether `caller` may act for `node`. Until stage 4: caller == node. It
    /// reads nothing about the node; whether `node` is registered is checked
    /// after it (D-39).
    function mayActFor(address caller, address node) external view returns (bool);
}

/// The Registry as stage 4 reads it (rulebook october-main-text-v3, R-26):
/// each node has one operator key, the node itself until a proposed key
/// accepts, and only that key acts for the node. It is declared beside
/// IRegistry, not on it, because setPayout now names the node and the stage
/// 3 Registry, which implements IRegistryV2, does not have the new shape;
/// spec S-10's rotation phase builds it into the Registry.
interface IRegistryV3 {
    /// Anyone, at any time, free. Registration is permanent; registering
    /// again changes nothing and is not refused.
    function register() external;

    /// Sets `node`'s payout address. address(0) makes it the node itself
    /// again. Refused with NOT_AUTHORIZED unless the caller is the node's
    /// operator key, then NOT_REGISTERED when `node` is not a node, and
    /// PAYOUT_INVALID when `payout` is the Broker's address. Was
    /// setPayout(address payout).
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
    /// checked after it (D-39).
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
