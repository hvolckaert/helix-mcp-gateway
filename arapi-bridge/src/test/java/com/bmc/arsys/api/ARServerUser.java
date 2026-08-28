package com.bmc.arsys.api;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class ARServerUser {
    private static final String SAMPLE_FORM = "Sample:Form";
    private static final String ERROR_FORM = "Error:Form";
    private static final String INITIAL_ENTRY_ID = "000000000000001";
    private static final String CREATED_ENTRY_ID = "000000000000999";
    private static final String TEST_USERNAME = "java-bridge-user";
    private static final String TEST_PASSWORD =
        "java-bridge-password-never-expose";
    private static final List<Field> SAMPLE_FIELDS = List.of(
        new Field(1, "Request ID", DataType.CHAR.getValue()),
        new Field(2, "Name", DataType.CHAR.getValue()),
        new Field(3, "Count", DataType.INTEGER.getValue()),
        new Field(4, "Enabled", DataType.ENUM.getValue()),
        new Field(5, "Description", DataType.CHAR.getValue()),
        new Field(
            Constants.AR_CORE_MODIFIED_DATE,
            "Modified Date",
            DataType.TIME.getValue()
        )
    );
    private static final Map<String, Entry> SAMPLE_ENTRIES =
        Collections.synchronizedMap(new LinkedHashMap<>());
    private static long modifiedDate = 1_000L;
    private static ARServerUser lastInstance;
    private static boolean administratorByDefault = true;

    static {
        Entry entry = new Entry();
        entry.put(1, new Value(INITIAL_ENTRY_ID));
        entry.put(2, new Value("sample-system"));
        entry.put(3, new Value(7));
        entry.put(4, new Value(1));
        entry.put(5, new Value("initial description"));
        entry.put(
            Constants.AR_CORE_MODIFIED_DATE,
            new Value(new Timestamp(modifiedDate))
        );
        SAMPLE_ENTRIES.put(INITIAL_ENTRY_ID, entry);
    }

    private Entry entry;
    private ServerInfoMap serverInfo = new ServerInfoMap();
    private List<String> forms = new ArrayList<>();
    private List<Field> fields = new ArrayList<>();
    private List<Entry> entries = new ArrayList<>();
    private String createResult = "000000000000001";
    private SQLResult sqlResult = new SQLResult();
    private boolean administrator = administratorByDefault;
    private String lastSql;
    private int lastSqlLimit;
    private boolean lastRetrieveNumMatches;
    private boolean loginCalled;
    private boolean logoutCalled;
    private boolean clearCalled;
    private boolean setEntryCalled;
    private Timestamp lastGetTime;
    private String lastQualification;
    private String configuredServer;
    private int configuredPort;
    private String configuredUser;
    private String configuredPassword;

    public ARServerUser() {
        lastInstance = this;
    }

    public static ARServerUser getLastInstance() {
        return lastInstance;
    }

    public static void setAdministratorDefaultForTest(boolean value) {
        administratorByDefault = value;
    }

    public void setServer(String value) {
        configuredServer = value;
    }

    public void setPort(int value) {
        configuredPort = value;
    }

    public void setUser(String value) {
        configuredUser = value;
    }

    public void setPassword(String value) {
        configuredPassword = value;
    }

    public void setAuthentication(String value) {
    }

    public void login() throws ARException {
        if (
            sampleDataEnabled()
            && (
                !"127.0.0.1".equals(configuredServer)
                || configuredPort != 46_000
                || !TEST_USERNAME.equals(configuredUser)
                || !TEST_PASSWORD.equals(configuredPassword)
            )
        ) {
            throw new ARException(List.of(new StatusInfo(623)));
        }
        loginCalled = true;
    }

    public void logout() {
        logoutCalled = true;
    }

    public void clear() {
        clearCalled = true;
        configuredServer = null;
        configuredPort = 0;
        configuredUser = null;
        configuredPassword = null;
    }

    public List<String> getListForm() throws ARException {
        if (sampleDataEnabled()) {
            return List.of(SAMPLE_FORM, ERROR_FORM);
        }
        return forms;
    }

    public List<Field> getListFieldObjects(String form) throws ARException {
        if (sampleDataEnabled()) {
            if (ERROR_FORM.equals(form)) {
                throw new ARException(List.of(new StatusInfo(302)));
            }
            if (SAMPLE_FORM.equals(form)) {
                return SAMPLE_FIELDS;
            }
            throw new ARException(List.of(new StatusInfo(303)));
        }
        return fields;
    }

    public QualifierInfo parseQualification(String form, String text)
        throws ARException {
        lastQualification = text;
        return new QualifierInfo(text);
    }

    public List<Entry> getListEntryObjects(
        String form,
        QualifierInfo qualification,
        int offset,
        int limit,
        List<SortInfo> sort,
        int[] fieldIds,
        boolean useLocale,
        OutputInteger total
    ) throws ARException {
        if (sampleDataEnabled()) {
            List<Entry> available;
            synchronized (SAMPLE_ENTRIES) {
                available = new ArrayList<>(SAMPLE_ENTRIES.values());
            }
            total.setValue(available.size());
            int start = Math.min(offset, available.size());
            int end = Math.min(start + limit, available.size());
            return new ArrayList<>(available.subList(start, end));
        }
        total.setValue(entries.size());
        return entries;
    }

    public Entry getEntry(String form, String entryId, int[] fieldIds)
        throws ARException {
        if (entry != null) {
            return entry;
        }
        if (sampleDataEnabled()) {
            Entry stored = SAMPLE_ENTRIES.get(entryId);
            if (stored == null) {
                throw new ARException(List.of(new StatusInfo(302)));
            }
            return stored;
        }
        return entry;
    }

    public String createEntry(String form, Entry values) throws ARException {
        if (sampleDataEnabled()) {
            Entry created = new Entry();
            created.putAll(values);
            created.put(1, new Value(CREATED_ENTRY_ID));
            created.put(
                Constants.AR_CORE_MODIFIED_DATE,
                new Value(new Timestamp(modifiedDate))
            );
            SAMPLE_ENTRIES.put(CREATED_ENTRY_ID, created);
            return CREATED_ENTRY_ID;
        }
        return createResult;
    }

    public boolean isAdministrator() throws ARException {
        return administrator;
    }

    public SQLResult getListSQL(
        String sql,
        int limit,
        boolean retrieveNumMatches
    ) throws ARException {
        lastSql = sql;
        lastSqlLimit = limit;
        lastRetrieveNumMatches = retrieveNumMatches;
        if (sampleDataEnabled() && sql.contains("sample_table")) {
            SQLResult sample = new SQLResult();
            sample.setContents(
                List.of(List.of(new Value(7), new Value("sample-system")))
            );
            return sample;
        }
        return sqlResult;
    }

    public ServerInfoMap getServerInfo(int[] requested) throws ARException {
        if (sampleDataEnabled()) {
            ServerInfoMap current = new ServerInfoMap();
            current.put(
                Constants.AR_SERVER_INFO_SERVER_TIME,
                new Value(new Timestamp(modifiedDate + 10))
            );
            return current;
        }
        return serverInfo;
    }

    public void setEntry(
        String form,
        String entryId,
        Entry values,
        Timestamp getTime,
        int option
    ) throws ARException {
        setEntryCalled = true;
        lastGetTime = getTime;
        if (sampleDataEnabled() && entry == null) {
            Entry stored = SAMPLE_ENTRIES.get(entryId);
            if (stored == null) {
                throw new ARException(List.of(new StatusInfo(302)));
            }
            stored.putAll(values);
            modifiedDate = getTime.getValue();
            stored.put(
                Constants.AR_CORE_MODIFIED_DATE,
                new Value(new Timestamp(modifiedDate))
            );
        }
    }

    public void setEntryForTest(Entry value) {
        entry = value;
    }

    public void setServerInfoForTest(ServerInfoMap value) {
        serverInfo = value;
    }

    public void setSqlResultForTest(SQLResult value) {
        sqlResult = value;
    }

    public void setAdministratorForTest(boolean value) {
        administrator = value;
    }

    public String getLastSql() {
        return lastSql;
    }

    public int getLastSqlLimit() {
        return lastSqlLimit;
    }

    public boolean isLastRetrieveNumMatches() {
        return lastRetrieveNumMatches;
    }

    public boolean isLoginCalled() {
        return loginCalled;
    }

    public boolean isLogoutCalled() {
        return logoutCalled;
    }

    public boolean isClearCalled() {
        return clearCalled;
    }

    public boolean isSetEntryCalled() {
        return setEntryCalled;
    }

    public Timestamp getLastGetTime() {
        return lastGetTime;
    }

    public String getLastQualification() {
        return lastQualification;
    }

    private static boolean sampleDataEnabled() {
        return "true".equals(System.getenv("HELIX_ARAPI_TEST_DATA"));
    }
}
