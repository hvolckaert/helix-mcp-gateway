package com.bmc.arsys.api;

public final class PermissionInfo {
    private final int groupId;
    private final int permission;
    public PermissionInfo(int groupId, int permission) {
        this.groupId = groupId;
        this.permission = permission;
    }
    public int getGroupID() { return groupId; }
    public int getPermissionValue() { return permission; }
}
