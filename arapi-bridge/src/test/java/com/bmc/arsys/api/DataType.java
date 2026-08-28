package com.bmc.arsys.api;

import java.util.HashMap;
import java.util.Map;

public final class DataType {
    private static final Map<Integer, DataType> VALUES = new HashMap<>();

    public static final DataType NULL = register(0);
    public static final DataType KEYWORD = register(1);
    public static final DataType INTEGER = register(2);
    public static final DataType REAL = register(3);
    public static final DataType CHAR = register(4);
    public static final DataType DIARY = register(5);
    public static final DataType ENUM = register(6);
    public static final DataType TIME = register(7);
    public static final DataType STATUS_HISTORY = register(8);
    public static final DataType BITMASK = register(9);
    public static final DataType BYTES = register(10);
    public static final DataType DECIMAL = register(11);
    public static final DataType ATTACHMENT = register(12);
    public static final DataType CURRENCY = register(13);
    public static final DataType DATE = register(14);
    public static final DataType TIME_OF_DAY = register(15);
    public static final DataType JOIN = register(16);
    public static final DataType TRIM = register(17);
    public static final DataType CONTROL = register(18);
    public static final DataType TABLE = register(19);
    public static final DataType COLUMN = register(20);
    public static final DataType PAGE = register(21);
    public static final DataType PAGE_HOLDER = register(22);
    public static final DataType ATTACHMENT_POOL = register(23);
    public static final DataType ULONG = register(24);
    public static final DataType COORDS = register(25);
    public static final DataType VIEW = register(26);
    public static final DataType DISPLAY = register(27);
    public static final DataType VALUELIST = register(28);

    private final int value;

    private DataType(int value) {
        this.value = value;
    }

    private static DataType register(int value) {
        DataType datatype = new DataType(value);
        VALUES.put(value, datatype);
        return datatype;
    }

    public static DataType toDataType(int value) {
        return VALUES.getOrDefault(value, new DataType(value));
    }

    public int getValue() {
        return value;
    }
}
