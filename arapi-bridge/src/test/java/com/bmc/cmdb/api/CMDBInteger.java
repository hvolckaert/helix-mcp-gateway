package com.bmc.cmdb.api;
public class CMDBInteger {
    private int value;
    public CMDBInteger(int value) { this.value = value; }
    public int intValue() { return value; }
    public void setValue(int n) { value = n; }
}
