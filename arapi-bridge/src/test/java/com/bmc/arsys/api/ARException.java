package com.bmc.arsys.api;

import java.util.List;

public class ARException extends Exception {
    private static final long serialVersionUID = 1L;
    private final transient List<StatusInfo> statuses;

    public ARException(List<StatusInfo> statuses) {
        this.statuses = statuses;
    }

    public List<StatusInfo> getLastStatus() {
        return statuses;
    }
}
