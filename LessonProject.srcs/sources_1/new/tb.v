`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/10/28 14:44:28
// Design Name: 
// Module Name: tb
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////

//测试数码管ip 
module  test(clk,data); 
input clk;
output [15:0]data;
//----------------------------------------------------------
//分频1Hz 
reg clk_1Hz;
integer clk_1Hz_cnt; 
always @(posedge clk)
if(clk_1Hz_cnt==32'd25000000-1)
begin 
	clk_1Hz_cnt <= 1'b0; 
	clk_1Hz <= ~clk_1Hz;
end else
	clk_1Hz_cnt <= clk_1Hz_cnt + 1'b1;
//----------------------------------------------------------
//循环显示0-9
reg [39:0]disp=40'h1234567890;
reg [15:0]data;
always @(posedge clk_1Hz) 
begin
	disp <= {disp[35:0],disp[39:36]}; 
	data <= disp[39:24];
end 
endmodule