// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// The stand-in for LPT: an ERC-20 with 18 decimals (rulebook R-09). Its
/// constructor creates a genesis supply for the deployer, outside every
/// round's issuance. After that only Claims, fixed at deployment, mints.
interface ITestLPT {
    function name() external view returns (string memory);
    function symbol() external view returns (string memory);

    /// 18.
    function decimals() external view returns (uint8);

    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function allowance(address owner, address spender) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);

    /// Only Claims; refused with NOT_AUTHORIZED from any other address.
    function mint(address to, uint256 amount) external;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    error NOT_AUTHORIZED();
}

/// The test LPT as stage 4 reads it (rulebook october-main-text-v4, R-16):
/// ITestLPT and a real burn. It is declared beside ITestLPT, not on it,
/// because the TestLPT built so far implements ITestLPT and has no burn;
/// spec S-07's contracts phase builds it into TestLPT.
interface ITestLPTV2 is ITestLPT {
    /// Anyone burns from its own balance: the balance and totalSupply fall
    /// by amount, with Transfer(caller, address(0), amount). Refused as a
    /// transfer is refused when amount is above the balance.
    function burn(uint256 amount) external;
}
