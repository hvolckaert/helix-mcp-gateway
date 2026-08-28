package com.bmc.arsys.api;

import java.util.ArrayList;
import java.util.List;

public final class SQLResult {
    private List<List<Value>> contents = new ArrayList<>();

    public List<List<Value>> getContents() {
        return contents;
    }

    public void setContents(List<List<Value>> value) {
        contents = value;
    }
}
