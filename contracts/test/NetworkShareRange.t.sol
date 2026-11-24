// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {FullFixture} from "./FullFixture.sol";
import {Broker} from "../src/money/Broker.sol";
import {vm} from "./Vm.sol";

/// The network share is stored off zero, and holds its whole range: with
/// the permitted full-network fee split a single funded ticket credits the
/// largest share there is, and draws take it back down to 0.
contract NetworkShareRangeTest is FullFixture {
    uint256 private constant GENESIS = 10_000_000e18;
    uint256 private constant MAX = type(uint256).max;

    function setUp() external {
        loadVectors("october-main-text-v5");
        vm.warp(1_000_000);
    }

    function test_the_largest_share_settles_and_draws_to_zero() external {
        deployFull(GENESIS, 0);
        uint256[] memory cuts = new uint256[](2);
        cuts[1] = 1_000_000;
        vm.prank(who("G"));
        params.announce(1, cuts);
        toRound(clock.currentRound() + 7);

        job(MAX);
        expectEq(broker.networkHeld(), MAX, "the whole ticket is the network's");
        draw(1);
        expectEq(broker.networkHeld(), MAX - 1, "one drawn");
        draw(MAX - 2);
        expectEq(broker.networkHeld(), 1, "all but one drawn");
        draw(1);
        expectEq(broker.networkHeld(), 0, "all drawn");
        expectEq(usdc.balanceOf(who("anyone")), MAX, "paid to");
    }

    function draw(uint256 amount) private {
        bytes memory d = abi.encodeCall(Broker.drawNetwork, (amount, who("anyone")));
        require(as_(address(buyback), address(broker), d).length == 0, "the Buyback draws");
    }
}
