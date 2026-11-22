// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// The values one buyback reads, once, for the round being bought
/// (rulebook R-16). In stage 4's first deployment FixedBuybackValues answers
/// with constants checked at construction; Parameters answers from its
/// groups 8 and 9 (R-25). A Buyback is deployed with one source and keeps it.
interface IBuybackValues {
    /// pool: the TestPool of this deployment's MockUSDC and TestLPT that the
    /// round buys from. burnPpm: the share of the LPT bought that is burned,
    /// over 10^6. twapSeconds: the TWAP interval. maxSlippagePpm: the bound
    /// over the TWAP, over 10^6. buyEveryRounds: the rounds from one buy to
    /// the next.
    struct BuybackValues {
        address pool;
        uint256 burnPpm;
        uint256 twapSeconds;
        uint256 maxSlippagePpm;
        uint256 buyEveryRounds;
    }

    /// The values in force for `round`.
    function buybackValuesAt(uint256 round) external view returns (BuybackValues memory);
}
