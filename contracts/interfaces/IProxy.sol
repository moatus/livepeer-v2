// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// The address an upgradeable contract is known by. It runs the code in
/// force, kept in the ERC-1967 implementation slot; only its admin, the
/// upgrade contract, replaces that code (rulebook R-27). The admin is fixed
/// in the proxy's code at creation and copied once to the ERC-1967 admin
/// slot for readers; the proxy checks the fixed one. Fixed.
///
/// Every call enters one fallback, which looks at the caller and the first
/// four bytes before decoding anything. A call from the admin with
/// upgradeToAndCall's selector is the proxy's own. Every other call, from
/// any address, the admin's other calls and another address's call with
/// that selector included, runs the code in force by delegatecall with its
/// calldata and value unchanged, and returns or reverts with that code's
/// bytes unchanged.
interface IProxy {
    /// ERC-1967's event, emitted when the code in force changes.
    event Upgraded(address indexed implementation);
    /// The admin's one call. Refuses an address without code, sets the
    /// implementation slot, emits Upgraded, then runs data in the new code
    /// by delegatecall unless data is empty, when nothing is called. If that
    /// call reverts, the whole call reverts with its bytes unchanged, and
    /// the code in force and the event are as before.
    function upgradeToAndCall(address implementation, bytes calldata data) external;
    error CODE_INVALID(address implementation);
}
