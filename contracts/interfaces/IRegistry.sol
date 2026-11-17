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
    /// Whether `caller` may act for `node`. Until stage 4: caller == node and
    /// node is registered.
    function mayActFor(address caller, address node) external view returns (bool);
}
