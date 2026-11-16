// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Smoke} from "../src/Smoke.sol";

/// Needs no test library: a failing require fails the test.
contract SmokeTest {
    function test_bump_adds_one() external {
        Smoke smoke = new Smoke();
        smoke.bump();
        require(smoke.count() == 1, "bump should add one");
    }
}
