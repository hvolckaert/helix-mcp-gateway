package com.bmc.arsys.api;

public final class EnumItem {
    private final String name;
    private final int number;

    public EnumItem(String name, int number) {
        this.name = name;
        this.number = number;
    }

    public String getEnumItemName() {
        return name;
    }

    public int getEnumItemNumber() {
        return number;
    }
}
