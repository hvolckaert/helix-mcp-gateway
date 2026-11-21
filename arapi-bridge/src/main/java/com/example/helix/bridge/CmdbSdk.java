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
import java.util.List;
import java.util.LinkedHashSet;

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
            List<String> projection = new java.util.ArrayList<>(List.of("DatasetId", "InstanceId", "ClassId"));
            if (className.equals("BMC_Dependency")) {
                for (String role : List.of("Source", "Destination")) {
                    String endpointDataset = stringValue(values.get(role + ".DatasetId"));
                    String endpointId = stringValue(values.get(role + ".InstanceId"));
                    String endpointClass = stringValue(values.get(role + ".ClassId"));
                    if (!dataset.equals(endpointDataset) || !endpointClass.equals(
                        get(user, "BMC.CORE:BMC_ComputerSystem", dataset, endpointId, List.of("ClassId")).get("ClassId"))) {
                        throw new IllegalStateException("CMDB endpoint identity differs");
                    }
                    for (String keyName : List.of("DatasetId", "InstanceId", "ClassId")) {
                        projection.add(role + "." + keyName);
                    }
                }
            }
            instance.create(user, dataset);
            CMDBInstance row = CMDBInstance.findByKey(user, key,
                CMDBInstance.CMDB_GET_DATASET_MODE_CURRENT, dataset, id,
                projection.toArray(String[]::new));
            if (!id.equals(instance.getId()) || row == null || !id.equals(row.getId())
                || !id.equals(attribute(row, "InstanceId")) || !dataset.equals(attribute(row, "DatasetId"))
                || row.getClassKey() == null || !className.equals(row.getClassKey().getClassName())
                || !"BMC.CORE".equals(row.getClassKey().getClassNamespace())) {
                throw new IllegalStateException("CMDB create identity could not be verified");
            }
            for (String name : projection) {
                if (name.contains(".") && !stringValue(values.get(name)).equals(attribute(row, name))) {
                    throw new IllegalStateException("stored CMDB relationship endpoint differs");
                }
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

    @Override
    public Map<String,Object> get(ARServerUser user, String form, String dataset, String id,
        List<String> fields) throws ARException {
        String className = form.substring("BMC.CORE:".length());
        CMDBClassNameKey key = new CMDBClassNameKey(className, "BMC.CORE");
        var projection = new LinkedHashSet<>(fields);
        projection.add("DatasetId");
        projection.add("InstanceId");
        projection.add("ClassId");
        CMDBInstance row = CMDBInstance.findByKey(user, key, CMDBInstance.CMDB_GET_DATASET_MODE_CURRENT,
            dataset, id, projection.toArray(String[]::new));
        if (row == null || row.getClassKey() == null
            || !className.equals(row.getClassKey().getClassName())
            || !"BMC.CORE".equals(row.getClassKey().getClassNamespace())
            || !id.equals(row.getId()) || !id.equals(attribute(row, "InstanceId"))
            || !dataset.equals(attribute(row, "DatasetId"))) {
            throw new IllegalStateException("CMDB instance identity unavailable");
        }
        attribute(row, "ClassId");
        Map<String,Object> result = new HashMap<>();
        for (String name : fields) {
            CMDBAttributeValue item = row.getAttributeValueByName(name);
            if (item == null) throw new IllegalStateException("CMDB attribute unavailable");
            Value value = item.getAttributeValue();
            Object raw = value == null ? null : value.getValue();
            if (raw instanceof java.math.BigDecimal decimal) raw = decimal.toPlainString();
            if (raw != null && !(raw instanceof String || raw instanceof Number || raw instanceof Boolean)) {
                throw new IllegalStateException("unsupported CMDB attribute type");
            }
            result.put(name, raw);
        }
        return result;
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
