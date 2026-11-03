module c17 (input N1, N2, N3, N6, N7, output N22, N23);
  wire N10, N11, N16, N19;
  nand g10 (N10, N1, N3);
  nand g11 (N11, N3, N6);
  nand g16 (N16, N2, N11);
  nand g19 (N19, N11, N7);
  nand g22 (N22, N10, N16);
  nand g23 (N23, N16, N19);
endmodule
