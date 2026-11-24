// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {PaymentsFixture} from "./PaymentsFixture.sol";
import {TestLPT} from "../src/money/TestLPT.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";
import {Claims} from "../src/money/Claims.sol";
import {Scores} from "../src/scores/Scores.sol";
import {TestPool} from "../src/money/TestPool.sol";
import {Buyback} from "../src/money/Buyback.sol";
import {IUpgrades} from "../interfaces/IUpgrades.sol";
import {IScoreSource} from "../interfaces/IScoreSource.sol";
import {IScores} from "../interfaces/IScores.sol";

/// The score a claim reads and a round's validator members, set by the
/// test: the stand-in for Scores where a shared case states them. A
/// `ledger` case states a claim's score and, under `members`, an earning
/// round's members, "a stand-in for the validator set" (rulebook, the
/// shared examples). Unset, a score reads 10^6, as a node nobody voted on
/// does, and a round has no members. Claims reads it through the two
/// views it calls, scoreOf and membersAt.
contract ScoreStub is IScoreSource {
    mapping(address => bool) private given;
    mapping(address => uint256) private score;
    mapping(uint256 => address[]) private members;

    function set(address node, uint256 s) external {
        given[node] = true;
        score[node] = s;
    }

    function setMembers(uint256 round, address[] calldata list) external {
        members[round] = list;
    }

    function scoreOf(address node) external view returns (uint256) {
        return given[node] ? score[node] : 1_000_000;
    }

    function membersAt(uint256 round) external view returns (address[] memory) {
        return members[round];
    }
}

/// A fresh payments deployment plus test LPT, StakeLedger and Claims, and
/// the steps a holder takes. Claims is created at the address the token
/// was given at deployment, as the Broker is at the Registry's. Claims
/// reads scores and members from the stub, or, deployed with
/// deployRewardsWithScores, from the real Scores: one contract as both
/// the score source and the validator set.
abstract contract RewardsFixture is PaymentsFixture {
    /// Test LPT's genesis supply, a test value outside every round's
    /// issuance. The test contract deploys the token, so it holds it.
    uint256 internal constant GENESIS = 1_000_000_000e18;

    TestLPT internal lpt;
    StakeLedger internal ledger;
    /// The stub, or address(0) when Claims reads the real Scores.
    ScoreStub internal scores;
    /// What Claims reads membersAt from: the stub or the real Scores.
    IScores internal validatorSet;
    Claims internal claims;
    /// With deploySystem only: the pool and the Buyback, Parameters'
    /// executor.
    TestPool internal pool;
    Buyback internal buyback;

    function deployRewards() internal {
        deployRewards(GENESIS);
    }

    function deployRewards(uint256 genesis) internal {
        deployRewards(genesis, false);
    }

    /// As deployRewards, with the real Scores as the deployment's score
    /// source and Claims' validator set.
    function deployRewardsWithScores() internal {
        deployRewards(GENESIS, true);
    }

    function deployRewards(uint256 genesis, bool real) internal {
        deployRewards(genesis, real, false);
    }

    /// The whole system the full demo deploys, every upgradeable contract
    /// behind its proxy: rewards with the real Scores, the pool, and the
    /// Buyback as Parameters' executor.
    function deploySystem() internal {
        deployRewards(GENESIS, true, true);
    }

    /// The five payments contracts, then the token, StakeLedger, the stub or
    /// Scores, the pool with `withBuyback`, Parameters and Claims, then the
    /// Buyback with `withBuyback`, then Upgrades: the Broker, StakeLedger and
    /// Scores are given the address Parameters will have, the token Claims',
    /// and Parameters the Buyback's.
    function deployRewards(uint256 genesis, bool real, bool withBuyback) internal {
        // deployBase's eight creations, the token, StakeLedger's code and
        // proxy, Scores' code and proxy or the stub, and the pool: then
        // Parameters' code and proxy, Claims' and the Buyback's.
        uint256 k = 8 + 1 + 2 + (real ? 2 : 1) + (withBuyback ? 1 : 0);
        address parameters = ahead(k + 1);
        address next = ahead(k + 3);
        address buybackAt = withBuyback ? ahead(k + 5) : address(0);
        deployBase(parameters);
        lpt = new TestLPT(next, genesis);
        ledger = StakeLedger(
            behind(address(new StakeLedger(lpt, registry, clock, params, upgradesAt)), "", IUpgrades.Side.LPT)
        );
        address source;
        if (real) {
            Scores s = Scores(behind(address(new Scores(registry, clock, ledger, params)), "", IUpgrades.Side.LPT));
            scores = ScoreStub(address(0));
            validatorSet = s;
            source = address(s);
        } else {
            scores = new ScoreStub();
            validatorSet = IScores(address(scores));
            source = address(scores);
        }
        pool = withBuyback ? new TestPool(address(usdc), address(lpt)) : TestPool(address(0));
        newParameters(source, address(pool), buybackAt);
        claims = Claims(
            behind(
                address(
                    new Claims(
                        lpt, registry, clock, broker, ledger, params, validatorSet, treasuryAddress(), upgradesAt
                    )
                ),
                "",
                IUpgrades.Side.LPT
            )
        );
        require(address(claims) == next, "Claims at the address the token was given");
        if (withBuyback) {
            buyback = Buyback(
                behind(
                    address(new Buyback(params, broker, clock, lpt, usdc, address(pool).codehash, upgradesAt)),
                    "",
                    IUpgrades.Side.LPT
                )
            );
            require(address(buyback) == buybackAt, "Buyback at the address Parameters was given");
        }
        newUpgrades();
    }

    function claimsAddress() internal view returns (address) {
        return address(claims);
    }

    /// The treasury address fixed at deployment (R-10): a test address.
    function treasuryAddress() internal pure returns (address) {
        return who("Treasury");
    }

    /// `holder` bonds `amount` to `node`, approving exactly that first; a
    /// refused bond leaves no approval behind.
    function bondAs(address holder, address node, uint256 amount) internal returns (bytes memory refused) {
        vm.prank(holder);
        lpt.approve(address(ledger), amount);
        refused = as_(holder, address(ledger), abi.encodeCall(StakeLedger.bond, (node, amount)));
        if (refused.length > 0) {
            vm.prank(holder);
            lpt.approve(address(ledger), 0);
        }
    }

    function expectRefused(bytes memory ret, string memory want, string memory what) internal pure {
        require(ret.length > 0, string.concat(what, ": took effect, want ", want));
        require(eq(code(ret), want), string.concat(what, ": refused with ", code(ret), ", want ", want));
    }

    function expectDone(bytes memory ret, string memory what) internal pure {
        require(ret.length == 0, string.concat(what, ": refused with ", code(ret)));
    }
}
