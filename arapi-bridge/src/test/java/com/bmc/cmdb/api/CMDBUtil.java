package com.bmc.cmdb.api;
import com.bmc.arsys.api.ARServerUser;
public class CMDBUtil {
    public static final int AR_COMMIT_CLIENT_TRANSACTION=1, AR_ROLLBACK_CLIENT_TRANSACTION=2;
    public static int action;
    public static boolean loseCommit;
    public static String CMDBCreateGuid(ARServerUser u) { return "RESERVED_GUID"; }
    public static String CMDBBeginClientManagedTransaction(ARServerUser u) { action=0; return "txn"; }
    public static void CMDBEndClientManagedTransaction(ARServerUser u, int mode) {
        action=mode;
        if (loseCommit && mode == AR_COMMIT_CLIENT_TRANSACTION) throw new IllegalStateException();
    }
}
