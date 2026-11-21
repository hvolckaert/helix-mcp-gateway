package com.example.helix.bridge;

import com.bmc.arsys.api.ARException;
import com.bmc.arsys.api.ARServerUser;
import com.bmc.arsys.api.Value;
import com.bmc.cmdb.api.CMDBAttributeValue;
import com.bmc.cmdb.api.CMDBClassNameKey;
import com.bmc.cmdb.api.CMDBInstance;
import com.bmc.cmdb.api.CMDBInteger;
import com.bmc.cmdb.api.CMDBUtil;
import java.util.HashMap;
import java.util.Map;

/** Optional provider, compiled only when the operator supplies the CMDB SDK. */
public final class CmdbSdk implements ArapiBridge.CmdbOperations {
    @Override
    public String reserve(ARServerUser user) throws ARException {
        return CMDBUtil.CMDBCreateGuid(user);
    }

    @Override
    public String create(ARServerUser user, String form, Map<String, Value> values)
        throws ARException {
        String dataset = stringValue(values.get("DatasetId"));
        String id = stringValue(values.get("InstanceId"));
        if (!id.matches("[A-Za-z0-9_-]{1,64}")) throw new IllegalArgumentException();
        String className = form.substring("BMC.CORE:".length());
        CMDBClassNameKey key = new CMDBClassNameKey(className, "BMC.CORE");
        Map<String, CMDBAttributeValue> attributes = new HashMap<>();
        values.forEach((name, value) -> attributes.put(name, new CMDBAttributeValue(name, value)));
        CMDBInstance instance = new CMDBInstance(key, attributes);
        instance.setId(id);
        CMDBUtil.CMDBBeginClientManagedTransaction(user);
        boolean commitStarted = false;
        try {
            CMDBInteger count = new CMDBInteger(-1);
            String[] existing = CMDBInstance.find(user, key, dataset,
                CMDBInstance.CMDB_GET_DATASET_MODE_CURRENT, "'InstanceId' = \"" + id + "\"",
                null, 0, 1, count);
            if (count.intValue() != 0 || (existing != null && existing.length != 0)) {
                throw new IllegalStateException("reserved instance identity is unavailable");
            }
            instance.create(user, dataset);
            CMDBInstance row = CMDBInstance.findByKey(user, key,
                CMDBInstance.CMDB_GET_DATASET_MODE_CURRENT, dataset, id,
                new String[] {"DatasetId", "InstanceId", "ClassId"});
            if (!id.equals(instance.getId()) || row == null || !id.equals(row.getId())
                || !id.equals(attribute(row, "InstanceId")) || !dataset.equals(attribute(row, "DatasetId"))
                || row.getClassKey() == null || !className.equals(row.getClassKey().getClassName())
                || !"BMC.CORE".equals(row.getClassKey().getClassNamespace())) {
                throw new IllegalStateException("CMDB create identity could not be verified");
            }
            String classId = attribute(row, "ClassId");
            commitStarted = true;
            CMDBUtil.CMDBEndClientManagedTransaction(user, CMDBUtil.AR_COMMIT_CLIENT_TRANSACTION);
            return classId;
        } finally {
            if (!commitStarted) {
                CMDBUtil.CMDBEndClientManagedTransaction(user, CMDBUtil.AR_ROLLBACK_CLIENT_TRANSACTION);
            }
        }
    }

    private static String attribute(CMDBInstance row, String name) {
        CMDBAttributeValue attribute = row.getAttributeValueByName(name);
        return stringValue(attribute == null ? null : attribute.getAttributeValue());
    }

    private static String stringValue(Value value) {
        if (value == null || !(value.getValue() instanceof String text) || text.isBlank()) {
            throw new IllegalArgumentException("required CMDB string is unavailable");
        }
        return text;
    }
}
