package com.bmc.arsys.api;

public final class CoordinateInfo {
    private final int x;
    private final int y;
    public CoordinateInfo(int x, int y) { this.x = x; this.y = y; }
    public int getXCoordinate() { return x; }
    public int getYCoordinate() { return y; }
}
