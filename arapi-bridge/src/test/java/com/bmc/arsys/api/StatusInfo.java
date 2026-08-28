package com.bmc.arsys.api;

public final class StatusInfo {
    private final int messageNum;

    public StatusInfo(int messageNum) {
        this.messageNum = messageNum;
    }

    public int getMessageNum() {
        return messageNum;
    }
}
