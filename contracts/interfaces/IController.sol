// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// The names the node looks up. Fixed at deployment; there is no owner and
/// no way to change a name later. (Spec S-09, section 3.4.)
interface IController {
    /// keccak256("BondingManager") returns the Registry, keccak256("RoundsManager")
    /// the RoundClock and keccak256("TicketBroker") the Broker. Any other name
    /// returns address(0).
    function getContract(bytes32 id) external view returns (address);
}
