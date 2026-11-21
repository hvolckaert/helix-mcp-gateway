package com.example.helix.bridge;
import com.bmc.arsys.api.ARServerUser;
import com.bmc.arsys.api.Value;
import com.bmc.cmdb.api.CMDBUtil;
import com.bmc.cmdb.api.CMDBInstance;
import java.util.Map;

public final class CmdbSdkTest {
    public static void main(String[] args) throws Exception {
        CmdbSdk sdk = new CmdbSdk();
        ARServerUser user = new ARServerUser();
        for (String name : new String[]{"BMC_ComputerSystem", "BMC_Dependency"}) {
            Map<String,Value> values = Map.of("InstanceId",new Value(sdk.reserve(user)),
                "DatasetId",new Value("BMC.SAMPLE"),"Name",new Value("same-name"));
            String form = "BMC.CORE:"+name;
            CMDBInstance.creates=0;
            require(name.equals(sdk.create(user,form,values)));
            require(CMDBInstance.creates==1 && CMDBUtil.action==CMDBUtil.AR_COMMIT_CLIENT_TRANSACTION);
            CMDBInstance.collision=true;
            try { sdk.create(user,form,values); throw new AssertionError(); }
            catch (IllegalStateException expected) { require(CMDBInstance.creates==1); }
            require(CMDBUtil.action==CMDBUtil.AR_ROLLBACK_CLIENT_TRANSACTION);
            CMDBInstance.collision=false;
            CMDBInstance.hideRead=true;
            try { sdk.create(user,form,values); throw new AssertionError(); }
            catch (IllegalStateException expected) { require(CMDBInstance.creates==2); }
            require(CMDBUtil.action==CMDBUtil.AR_ROLLBACK_CLIENT_TRANSACTION);
            CMDBInstance.hideRead=false;
            CMDBUtil.loseCommit=true;
            try { sdk.create(user,form,values); throw new AssertionError(); }
            catch (IllegalStateException expected) { require(CMDBInstance.creates==3); }
            require(CMDBUtil.action==CMDBUtil.AR_COMMIT_CLIENT_TRANSACTION);
            CMDBUtil.loseCommit=false;
        }
    }
    private static void require(boolean ok) { if(!ok) throw new AssertionError(); }
}
