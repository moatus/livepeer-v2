// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// Proves the contract toolchain builds and tests. It holds no rule. Delete
/// it when the first real contract and its tests arrive.
contract Smoke {
    uint256 public count;

    function bump() external {
        count += 1;
    }
}
