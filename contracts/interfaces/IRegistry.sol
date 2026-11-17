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
