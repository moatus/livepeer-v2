// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// Holds every bonded and unbonded LPT and answers stake by round (rulebook
/// R-08, R-15, R-24). A bond, an unbond or a rebond made in round R counts
/// from R + 1; a transfer made in round R counts for the old node through
/// R + 20 and for the new one from R + 21. Stake at the start of a round
/// never changes once that round has started.
interface IStakeLedger {
    /// Bonds `amount` of the caller's LPT to `node`, read at execution.
    /// Refused with NOT_REGISTERED when `node` is not a registered node.
    function bond(address node, uint256 amount) external;

    /// Unbonds from the caller's bond staying at `node`: bonded, rebonded or
    /// arrived, and not leaving. Withdrawable from 21 rounds on. Refused
    /// with AMOUNT_TOO_LARGE above that bond.
    function unbond(address node, uint256 amount) external;

    /// Moves `amount` of the caller's bond staying at `from` to `to`, where it
    /// counts 21 rounds on; it cannot be redirected. Refused with
    /// NOT_REGISTERED when `to` is not a registered node, with SAME_NODE
    /// when `to` is `from`, and with AMOUNT_TOO_LARGE above the bond staying
    /// at `from`, the first that applies; a refused transfer records nothing.
    function transferBond(address from, address to, uint256 amount) external;

    /// Unbonds stake the caller has in transfer from `from` to `to`, the
    /// transfer arriving last first. It stops counting for `from` next round
    /// and never arrives. Refused with SAME_NODE when `to` is `from`, and
    /// with AMOUNT_TOO_LARGE above what is in transfer.
    function unbondTransfer(address from, address to, uint256 amount) external;

    /// Returns unbonded LPT that was counting for `node` and is not yet
    /// withdrawable, the latest unbond first. Refused with NOT_REGISTERED
    /// when `node` is not a registered node, and with AMOUNT_TOO_LARGE above
    /// what is waiting for `node`.
    function rebond(address node, uint256 amount) external;

    /// Pays the caller every withdrawable unbond at once. Refused with
    /// UNBOND_NOT_READY when none is withdrawable.
    function withdrawUnbonded() external;

    /// 0 to 10^6, counting from the next round. Refused, the first that
    /// applies, with NOT_AUTHORIZED unless the Registry's mayActFor(caller,
    /// node), with NOT_REGISTERED when `node` is not a registered node, and
    /// with CUT_OUT_OF_RANGE above 10^6.
    function setRewardCut(address node, uint256 cut) external;

    /// At the start of `round`.
    function stakeAt(address node, uint256 round) external view returns (uint256);
    function totalStakeAt(uint256 round) external view returns (uint256);
    function bondAt(address holder, address node, uint256 round) external view returns (uint256);
    function cutAt(address node, uint256 round) external view returns (uint256);

    /// As they stand now.
    function stayingOf(address holder, address node) external view returns (uint256);
    function inTransferOf(address holder, address from, address to) external view returns (uint256);
    function unbondedOf(address holder) external view returns (uint256 waiting, uint256 ready);
    function rebondableOf(address holder, address node) external view returns (uint256);

    event Bonded(address indexed holder, address indexed node, uint256 amount, uint256 fromRound);
    event Unbonded(address indexed holder, address indexed node, uint256 amount, uint256 readyRound);
    event TransferStarted(
        address indexed holder, address indexed from, address indexed to, uint256 amount, uint256 arrivalRound
    );
    event TransferUnbonded(
        address indexed holder, address indexed from, address indexed to, uint256 amount, uint256 readyRound
    );
    event Rebonded(address indexed holder, address indexed node, uint256 amount, uint256 fromRound);
    event UnbondedWithdrawn(address indexed holder, uint256 amount);
    event RewardCutSet(address indexed node, uint256 cut, uint256 fromRound);

    error NOT_REGISTERED();
    error NOT_AUTHORIZED();
    error AMOUNT_TOO_LARGE();
    error UNBOND_NOT_READY();
    error CUT_OUT_OF_RANGE();
    error SAME_NODE();
}
