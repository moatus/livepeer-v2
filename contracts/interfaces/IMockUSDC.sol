// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// The stand-in for USDC: an ERC-20 with six decimals and an open mint.
interface IMockUSDC {
    function name() external view returns (string memory);
    function symbol() external view returns (string memory);

    /// 6.
    function decimals() external view returns (uint8);

    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function allowance(address owner, address spender) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);

    /// Open: anyone may mint any amount to anyone. A test token only.
    function mint(address to, uint256 amount) external;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
}
