package com.bmc.cmdb.api;
import com.bmc.arsys.api.ARServerUser;
import com.bmc.arsys.api.Value;
import java.util.Map;
public class CMDBInstance {
    public static final int CMDB_GET_DATASET_MODE_CURRENT=0;
    public static boolean collision, hideRead, swapEndpoints;
    public static int creates;
    private static CMDBInstance row;
    public static String[] projection;
    private final CMDBClassNameKey key;
    private final Map<String,CMDBAttributeValue> attrs;
    private String id;
    public CMDBInstance(CMDBClassNameKey key, Map<String,CMDBAttributeValue> attrs) {
        this.key=key; this.attrs=attrs;
    }
    public void setId(String value) { id=value; }
    public String getId() { return id; }
    public CMDBClassNameKey getClassKey() { return key; }
    public CMDBAttributeValue getAttributeValueByName(String field) { return attrs.get(field); }
    public void create(ARServerUser u, String ds) {
        creates++;
        attrs.put("ClassId",new CMDBAttributeValue("ClassId",new Value(key.getClassName())));
        row=this;
    }
    public static String[] find(ARServerUser u, CMDBClassNameKey k, String ds, int mode,
        String qualification,Object sort,int offset,int limit,CMDBInteger count) {
        count.setValue(collision ? 1 : 0);
        return collision ? new String[]{"EXISTING"} : null;
    }
    public static CMDBInstance findByKey(ARServerUser u, CMDBClassNameKey k, int mode,
        String ds,String id,String[] fields) {
        projection = fields;
        if (k.getClassName().equals("BMC_ComputerSystem") && (id.equals("SRC") || id.equals("DST"))) {
            CMDBInstance endpoint = new CMDBInstance(k, Map.of(
                "InstanceId", new CMDBAttributeValue("InstanceId", new Value(id)),
                "DatasetId", new CMDBAttributeValue("DatasetId", new Value(ds)),
                "ClassId", new CMDBAttributeValue("ClassId", new Value("CI-CLASS"))));
            endpoint.setId(id);
            return endpoint;
        }
        if (swapEndpoints && row != null) {
            row.attrs.put("Source.InstanceId", new CMDBAttributeValue("Source.InstanceId", new Value("DST")));
        }
        return hideRead ? null : row;
    }
}
