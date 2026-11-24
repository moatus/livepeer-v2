// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IProxy} from "../../interfaces/IProxy.sol";

/// The address an upgradeable contract is known by (rulebook R-27, IProxy).
/// It runs the code in force, kept in ERC-1967's implementation slot. Its
/// admin, the upgrade contract, is fixed in this code at creation and
/// copied once to ERC-1967's admin slot for readers; the fallback checks the
/// fixed one, so no code in force can change who replaces it.
///
/// It declares no function: every call enters the fallback, which looks at
/// the caller and the first four bytes before decoding anything. Only the
/// admin's call with upgradeToAndCall's selector is the proxy's own. Every
/// other call runs the code in force by delegatecall, with its calldata and
/// value unchanged, and returns or reverts with that code's bytes.
contract Proxy {
    /// ERC-1967: bytes32(uint256(keccak256("eip1967.proxy.implementation")) - 1).
    bytes32 private constant IMPLEMENTATION = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
    /// ERC-1967: bytes32(uint256(keccak256("eip1967.proxy.admin")) - 1).
    bytes32 private constant ADMIN = 0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103;

    address private immutable admin;

    /// Runs `data` in `code` unless it is empty; a refusal there reverts the
    /// creation with its bytes, so no proxy is left uninitialised.
    constructor(address code, address admin_, bytes memory data) {
        admin = admin_;
        assembly {
            sstore(ADMIN, admin_)
        }
        _replace(code, data);
    }

    fallback() external payable {
        if (msg.sender == admin && msg.sig == IProxy.upgradeToAndCall.selector) {
            (address code, bytes memory data) = abi.decode(msg.data[4:], (address, bytes));
            _replace(code, data);
            return;
        }
        assembly {
            calldatacopy(0, 0, calldatasize())
            let ok := delegatecall(gas(), sload(IMPLEMENTATION), 0, calldatasize(), 0, 0)
            returndatacopy(0, 0, returndatasize())
            if iszero(ok) { revert(0, returndatasize()) }
            return(0, returndatasize())
        }
    }

    /// Refuses an address without code, puts `code` in force, then runs
    /// `data` in it unless `data` is empty. A refusal of that call reverts
    /// the whole call with its bytes unchanged.
    function _replace(address code, bytes memory data) private {
        if (code.code.length == 0) revert IProxy.CODE_INVALID(code);
        assembly {
            sstore(IMPLEMENTATION, code)
        }
        emit IProxy.Upgraded(code);
        if (data.length == 0) return;
        (bool ok, bytes memory refused) = code.delegatecall(data);
        if (!ok) {
            assembly {
                revert(add(refused, 32), mload(refused))
            }
        }
    }
}
