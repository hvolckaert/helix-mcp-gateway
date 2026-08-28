package com.example.helix.bridge;

import com.bmc.arsys.api.ARException;
import com.bmc.arsys.api.ARServerUser;
import com.bmc.arsys.api.ARErrors;
import com.bmc.arsys.api.Constants;
import com.bmc.arsys.api.DataType;
import com.bmc.arsys.api.DateInfo;
import com.bmc.arsys.api.Entry;
import com.bmc.arsys.api.Field;
import com.bmc.arsys.api.OutputInteger;
import com.bmc.arsys.api.QualifierInfo;
import com.bmc.arsys.api.ServerInfoMap;
import com.bmc.arsys.api.SortInfo;
import com.bmc.arsys.api.SQLResult;
import com.bmc.arsys.api.StatusInfo;
import com.bmc.arsys.api.Time;
import com.bmc.arsys.api.Timestamp;
import com.bmc.arsys.api.Value;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.Executors;

public final class ArapiBridge {
    private static final int DEFAULT_PORT = 8090;
    private static final int DEFAULT_THREADS = 4;
    private static final int MAX_BODY_BYTES = 65_536;
    private static final int MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
    private static final int MAX_FORMS = 100_000;
    private static final int MAX_FIELDS = 100_000;
    private static final int MAX_SELECTED_FIELDS = 128;
    private static final int MAX_WRITE_FIELDS = 32;
    private static final int MAX_SORT_FIELDS = 8;
    private static final int MAX_ENTRIES = 100_000;
    private static final int MAX_OFFSET = 10_000_000;
    private static final int MAX_NAME_LENGTH = 255;
    private static final int MAX_QUALIFICATION_LENGTH = 8_192;
    private static final int MAX_SQL_LENGTH = 32_768;
    private static final int MAX_WRITE_VALUE_LENGTH = 8_192;
    private static final long MAX_TIMESTAMP = 253_402_300_799L;
    private static final int UPDATE_LOCK_STRIPES = 64;
    private static final Object[] UPDATE_LOCKS = createUpdateLocks();

    private ArapiBridge() {
    }

    public static void main(String[] args) throws IOException {
        int port = readBoundedInteger(
            "HELIX_ARAPI_BRIDGE_PORT",
            DEFAULT_PORT,
            1,
            65_535
        );
        int threads = readBoundedInteger(
            "HELIX_ARAPI_BRIDGE_THREADS",
            DEFAULT_THREADS,
            1,
            32
        );
        HttpServer server = HttpServer.create(
            new InetSocketAddress("127.0.0.1", port),
            0
        );
        server.createContext("/health", new HealthHandler());
        server.createContext("/v1/forms", new FormsHandler());
        server.createContext("/v1/fields", new FieldsHandler());
        server.createContext("/v1/entries/query", new QueryEntriesHandler());
        server.createContext("/v1/entries/get", new GetEntryHandler());
        server.createContext(
            "/v1/entries/prepare-update",
            new PrepareUpdateHandler()
        );
        server.createContext("/v1/entries/create", new CreateEntryHandler());
        server.createContext("/v1/entries/update", new UpdateEntryHandler());
        server.createContext("/v1/sql/query", new SqlQueryHandler());
        server.setExecutor(Executors.newFixedThreadPool(threads));
        server.start();
        System.err.println(
            "Helix ARAPI bridge listening on 127.0.0.1:" + port
        );
    }

    private static int readBoundedInteger(
        String name,
        int defaultValue,
        int minimum,
        int maximum
    ) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) {
            return defaultValue;
        }
        try {
            int parsed = Integer.parseInt(value);
            if (parsed < minimum || parsed > maximum) {
                throw new IllegalArgumentException(name + " is out of range");
            }
            return parsed;
        } catch (NumberFormatException error) {
            throw new IllegalArgumentException(name + " is invalid");
        }
    }

    private static final class HealthHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            if (
                !"/health".equals(exchange.getRequestURI().getPath())
                || !"GET".equals(exchange.getRequestMethod())
            ) {
                respond(exchange, 405, "{\"error\":\"method not allowed\"}");
                return;
            }
            respond(exchange, 200, "{\"status\":\"ok\"}");
        }
    }

    private abstract static class ArapiHandler implements HttpHandler {
        private final String path;

        ArapiHandler(String path) {
            this.path = path;
        }

        @Override
        public final void handle(HttpExchange exchange) throws IOException {
            if (
                !path.equals(exchange.getRequestURI().getPath())
                || !"POST".equals(exchange.getRequestMethod())
            ) {
                respond(exchange, 405, "{\"error\":\"method not allowed\"}");
                return;
            }
            try {
                Map<String, String> input = readFormBody(exchange);
                String payload = withUser(input, user -> execute(user, input));
                respond(exchange, 200, payload);
            } catch (AmbiguousField error) {
                respond(
                    exchange,
                    400,
                    "{\"error\":\"ambiguous field name\","
                        + "\"code\":\"FORM_FIELD_AMBIGUOUS\"}"
                );
            } catch (BadRequest error) {
                respond(exchange, 400, "{\"error\":\"invalid request\"}");
            } catch (Conflict error) {
                respond(
                    exchange,
                    409,
                    "{\"error\":\"entry changed after it was read\"}"
                );
            } catch (AdminRequired error) {
                respond(
                    exchange,
                    403,
                    "{\"error\":\"ARAPI administrator permission required\","
                        + "\"code\":\"ARAPI_ADMIN_REQUIRED\"}"
                );
            } catch (ARException error) {
                if (hasArapiError(error, ARErrors.AR_ERROR_MUST_BE_ADMIN)) {
                    respond(
                        exchange,
                        403,
                        "{\"error\":\"ARAPI administrator permission required\","
                            + "\"code\":\"ARAPI_ADMIN_REQUIRED\"}"
                    );
                    return;
                }
                int status = hasArapiError(
                    error,
                    ARErrors.AR_ERROR_MODIFIED_SINCE_GET
                ) ? 409 : 502;
                respond(exchange, status, encodeArapiError(error));
            } catch (RuntimeException error) {
                respond(exchange, 500, "{\"error\":\"bridge operation failed\"}");
            }
        }

        protected abstract String execute(
            ARServerUser user,
            Map<String, String> input
        ) throws ARException, BadRequest, AdminRequired;
    }

    private static final class SqlQueryHandler extends ArapiHandler {
        SqlQueryHandler() {
            super("/v1/sql/query");
        }

        @Override
        protected String execute(
            ARServerUser user,
            Map<String, String> input
        ) throws ARException, BadRequest, AdminRequired {
            String sql = requiredSql(input, "sql", MAX_SQL_LENGTH);
            if (!isReadOnlySql(sql)) {
                throw new BadRequest();
            }
            int limit = boundedInteger(input, "limit", 1, MAX_ENTRIES);
            int columnCount = boundedInteger(
                input,
                "column_count",
                1,
                MAX_SELECTED_FIELDS
            );
            if (!user.isAdministrator()) {
                throw new AdminRequired();
            }
            SQLResult result = user.getListSQL(sql, limit + 1, false);
            List<List<Value>> rows = result == null
                ? List.of()
                : result.getContents();
            if (rows == null) {
                rows = List.of();
            }
            if (rows.size() > limit + 1) {
                throw new IllegalStateException(
                    "ARAPI returned too many SQL rows"
                );
            }
            return encodeSqlRows(rows, limit, columnCount);
        }
    }

    private static final class FormsHandler extends ArapiHandler {
        FormsHandler() {
            super("/v1/forms");
        }

        @Override
        protected String execute(
            ARServerUser user,
            Map<String, String> input
        ) throws ARException {
            List<String> forms = new ArrayList<>(user.getListForm());
            if (forms.size() > MAX_FORMS) {
                throw new IllegalStateException("form catalog is too large");
            }
            forms.sort(String.CASE_INSENSITIVE_ORDER);
            return encodeForms(forms);
        }
    }

    private static final class FieldsHandler extends ArapiHandler {
        FieldsHandler() {
            super("/v1/fields");
        }

        @Override
        protected String execute(
            ARServerUser user,
            Map<String, String> input
        ) throws ARException, BadRequest {
            String form = requiredText(input, "form", MAX_NAME_LENGTH);
            List<Field> fields = loadFields(user, form);
            return encodeFields(fields);
        }
    }

    private static final class QueryEntriesHandler extends ArapiHandler {
        QueryEntriesHandler() {
            super("/v1/entries/query");
        }

        @Override
        protected String execute(
            ARServerUser user,
            Map<String, String> input
        ) throws ARException, BadRequest {
            String form = requiredText(input, "form", MAX_NAME_LENGTH);
            List<Field> fields = loadFields(user, form);
            FieldSelection selected = selectFields(
                fields,
                requiredText(input, "fields", MAX_BODY_BYTES),
                MAX_SELECTED_FIELDS
            );
            int offset = boundedInteger(input, "offset", 0, MAX_OFFSET);
            int limit = boundedInteger(input, "limit", 1, MAX_ENTRIES);
            boolean includeTotal = parseBoolean(
                input.getOrDefault("include_total", "false")
            );
            String qualificationText = optionalText(
                input,
                "qualification",
                MAX_QUALIFICATION_LENGTH
            );
            QualifierInfo qualification = qualificationText.isBlank()
                ? matchAllQualification(user, form, fields)
                : user.parseQualification(form, qualificationText);
            List<SortInfo> sort = parseSort(
                input.getOrDefault("sort", ""),
                fields
            );
            OutputInteger total = new OutputInteger();
            List<Entry> entries = user.getListEntryObjects(
                form,
                qualification,
                offset,
                limit,
                sort,
                selected.ids,
                false,
                total
            );
            if (entries.size() > limit) {
                throw new IllegalStateException(
                    "ARAPI returned too many entries"
                );
            }
            return encodeEntries(
                entries,
                selected,
                offset,
                limit,
                includeTotal ? total.longValue() : null
            );
        }
    }

    private static final class GetEntryHandler extends ArapiHandler {
        GetEntryHandler() {
            super("/v1/entries/get");
        }

        @Override
        protected String execute(
            ARServerUser user,
            Map<String, String> input
        ) throws ARException, BadRequest {
            String form = requiredText(input, "form", MAX_NAME_LENGTH);
            String entryId = requiredText(
                input,
                "entry_id",
                MAX_NAME_LENGTH
            );
            List<Field> fields = loadFields(user, form);
            FieldSelection selected = selectFields(
                fields,
                requiredText(input, "fields", MAX_BODY_BYTES),
                MAX_SELECTED_FIELDS
            );
            Entry entry = user.getEntry(form, entryId, selected.ids);
            return encodeEntryResult(entryId, entry, selected);
        }
    }

    private static final class PrepareUpdateHandler extends ArapiHandler {
        PrepareUpdateHandler() {
            super("/v1/entries/prepare-update");
        }

        @Override
        protected String execute(
            ARServerUser user,
            Map<String, String> input
        ) throws ARException, BadRequest {
            String form = requiredText(input, "form", MAX_NAME_LENGTH);
            String entryId = requiredText(
                input,
                "entry_id",
                MAX_NAME_LENGTH
            );
            List<Field> fields = loadFields(user, form);
            FieldSelection selected = selectFields(
                fields,
                requiredText(input, "fields", MAX_BODY_BYTES),
                MAX_WRITE_FIELDS
            );
            Entry entry = user.getEntry(
                form,
                entryId,
                includeModifiedDate(selected.ids)
            );
            long precondition = readModifiedDate(entry);
            return encodePreparedEntry(
                entryId,
                entry,
                selected,
                precondition
            );
        }
    }

    private static final class CreateEntryHandler extends ArapiHandler {
        CreateEntryHandler() {
            super("/v1/entries/create");
        }

        @Override
        protected String execute(
            ARServerUser user,
            Map<String, String> input
        ) throws ARException, BadRequest {
            String form = requiredText(input, "form", MAX_NAME_LENGTH);
            List<Field> fields = loadFields(user, form);
            Entry values = parseWriteValues(input, fields);
            String entryId = user.createEntry(form, values);
            return encodeCreateResult(entryId);
        }
    }

    private static final class UpdateEntryHandler extends ArapiHandler {
        UpdateEntryHandler() {
            super("/v1/entries/update");
        }

        @Override
        protected String execute(
            ARServerUser user,
            Map<String, String> input
        ) throws ARException, BadRequest {
            String form = requiredText(input, "form", MAX_NAME_LENGTH);
            String entryId = requiredText(
                input,
                "entry_id",
                MAX_NAME_LENGTH
            );
            long precondition = boundedLong(
                input,
                "precondition",
                1,
                MAX_TIMESTAMP
            );
            List<Field> fields = loadFields(user, form);
            Entry values = parseWriteValues(input, fields);
            conditionallyUpdate(
                user,
                form,
                entryId,
                values,
                precondition
            );
            return encodeWriteResult(entryId);
        }
    }

    private static <T> T withUser(
        Map<String, String> input,
        UserOperation<T> operation
    ) throws ARException, BadRequest, AdminRequired {
        String host = required(input, "host");
        int port = parsePort(required(input, "port"));
        String username = required(input, "username");
        String password = required(input, "password");
        String authentication = input.getOrDefault("authentication", "");
        ARServerUser user = new ARServerUser();
        try {
            user.setServer(host);
            user.setPort(port);
            user.setUser(username);
            user.setPassword(password);
            if (!authentication.isBlank()) {
                user.setAuthentication(authentication);
            }
            user.login();
            return operation.execute(user);
        } finally {
            user.logout();
            user.clear();
        }
    }

    private static List<Field> loadFields(
        ARServerUser user,
        String form
    ) throws ARException {
        List<Field> fields = new ArrayList<>(user.getListFieldObjects(form));
        if (fields.size() > MAX_FIELDS) {
            throw new IllegalStateException("field catalog is too large");
        }
        fields.sort(
            Comparator.comparingInt(Field::getFieldID)
                .thenComparing(Field::getName, String.CASE_INSENSITIVE_ORDER)
        );
        return fields;
    }

    private static long readServerTime(ARServerUser user)
        throws ARException {
        ServerInfoMap info = user.getServerInfo(
            new int[] {Constants.AR_SERVER_INFO_SERVER_TIME}
        );
        Value value = info.get(Constants.AR_SERVER_INFO_SERVER_TIME);
        if (value == null) {
            throw new IllegalStateException("server time is unavailable");
        }
        Object raw = value.getValue();
        long timestamp;
        if (raw instanceof Timestamp arTimestamp) {
            timestamp = arTimestamp.getValue();
        } else if (raw instanceof Number number) {
            timestamp = number.longValue();
        } else {
            throw new IllegalStateException("server time is invalid");
        }
        if (timestamp < 1 || timestamp > MAX_TIMESTAMP) {
            throw new IllegalStateException("server time is invalid");
        }
        return timestamp;
    }

    private static int[] includeModifiedDate(int[] selected) {
        for (int fieldId : selected) {
            if (fieldId == Constants.AR_CORE_MODIFIED_DATE) {
                return selected;
            }
        }
        int[] fields = new int[selected.length + 1];
        System.arraycopy(selected, 0, fields, 0, selected.length);
        fields[selected.length] = Constants.AR_CORE_MODIFIED_DATE;
        return fields;
    }

    private static long readModifiedDate(Entry entry) {
        Value value = entry.get(Constants.AR_CORE_MODIFIED_DATE);
        if (value == null) {
            throw new IllegalStateException("modified date is unavailable");
        }
        Object raw = value.getValue();
        long timestamp;
        if (raw instanceof Timestamp arTimestamp) {
            timestamp = arTimestamp.getValue();
        } else if (raw instanceof Number number) {
            timestamp = number.longValue();
        } else {
            throw new IllegalStateException("modified date is invalid");
        }
        if (timestamp < 1 || timestamp > MAX_TIMESTAMP) {
            throw new IllegalStateException("modified date is invalid");
        }
        return timestamp;
    }

    private static void conditionallyUpdate(
        ARServerUser user,
        String form,
        String entryId,
        Entry values,
        long precondition
    ) throws ARException {
        synchronized (updateLock(form, entryId)) {
            Entry current = user.getEntry(
                form,
                entryId,
                new int[] {Constants.AR_CORE_MODIFIED_DATE}
            );
            long modifiedDate = readModifiedDate(current);
            if (modifiedDate != precondition) {
                throw new Conflict();
            }
            long getTime = readServerTime(user);
            if (getTime <= modifiedDate) {
                getTime = modifiedDate + 1;
            }
            user.setEntry(
                form,
                entryId,
                values,
                new Timestamp(getTime),
                Constants.AR_JOIN_SETOPTION_NONE
            );
        }
    }

    private static Object[] createUpdateLocks() {
        Object[] locks = new Object[UPDATE_LOCK_STRIPES];
        for (int index = 0; index < locks.length; index++) {
            locks[index] = new Object();
        }
        return locks;
    }

    private static Object updateLock(String form, String entryId) {
        int hash = 31 * form.hashCode() + entryId.hashCode();
        return UPDATE_LOCKS[Math.floorMod(hash, UPDATE_LOCKS.length)];
    }

    private static Entry parseWriteValues(
        Map<String, String> input,
        List<Field> fields
    ) throws BadRequest {
        int count = boundedInteger(
            input,
            "value_count",
            1,
            MAX_WRITE_FIELDS
        );
        Map<String, Field> byName = indexFields(fields);
        Map<String, Boolean> seen = new HashMap<>();
        Entry values = new Entry();
        for (int index = 0; index < count; index++) {
            String suffix = "_" + index;
            String name = requiredText(
                input,
                "field" + suffix,
                MAX_NAME_LENGTH
            );
            String canonical = name.toLowerCase(Locale.ROOT);
            if (seen.putIfAbsent(canonical, Boolean.TRUE) != null) {
                throw new BadRequest();
            }
            Field field = resolveField(byName, canonical);
            String valueType = requiredText(
                input,
                "value_type" + suffix,
                16
            );
            String rawValue = requiredRaw(
                input,
                "value" + suffix,
                MAX_WRITE_VALUE_LENGTH
            );
            values.put(
                field.getFieldID(),
                parseWriteValue(field, valueType, rawValue)
            );
        }
        return values;
    }

    private static Value parseWriteValue(
        Field field,
        String valueType,
        String rawValue
    ) throws BadRequest {
        DataType datatype = DataType.toDataType(field.getDataType());
        if (DataType.CHAR.equals(datatype)) {
            requireValueType(valueType, "string");
            return new Value(rawValue);
        }
        if (DataType.DIARY.equals(datatype)) {
            requireValueType(valueType, "string");
            return new Value(rawValue, DataType.DIARY);
        }
        if (
            DataType.INTEGER.equals(datatype)
            || DataType.ENUM.equals(datatype)
            || DataType.BITMASK.equals(datatype)
        ) {
            if ("boolean".equals(valueType)) {
                return new Value(parseBoolean(rawValue) ? 1 : 0);
            }
            requireValueType(valueType, "integer");
            return new Value(parseInteger(
                rawValue,
                Integer.MIN_VALUE,
                Integer.MAX_VALUE
            ));
        }
        if (DataType.REAL.equals(datatype)) {
            if (
                !"integer".equals(valueType)
                && !"number".equals(valueType)
            ) {
                throw new BadRequest();
            }
            return new Value(parseFiniteDouble(rawValue));
        }
        if (DataType.DECIMAL.equals(datatype)) {
            if (
                !"integer".equals(valueType)
                && !"number".equals(valueType)
            ) {
                throw new BadRequest();
            }
            try {
                return new Value(new BigDecimal(rawValue));
            } catch (NumberFormatException error) {
                throw new BadRequest();
            }
        }
        if (DataType.ULONG.equals(datatype)) {
            requireValueType(valueType, "integer");
            long number = parseLong(rawValue, 0, Long.MAX_VALUE);
            return new Value(number, DataType.ULONG);
        }
        if (DataType.TIME.equals(datatype)) {
            requireValueType(valueType, "integer");
            long number = parseLong(rawValue, 0, MAX_TIMESTAMP);
            return new Value(new Timestamp(number));
        }
        if (DataType.DATE.equals(datatype)) {
            requireValueType(valueType, "integer");
            return new Value(new DateInfo(parseInteger(
                rawValue,
                0,
                Integer.MAX_VALUE
            )));
        }
        if (DataType.TIME_OF_DAY.equals(datatype)) {
            requireValueType(valueType, "integer");
            return new Value(new Time(parseLong(rawValue, 0, 86_399)));
        }
        throw new BadRequest();
    }

    private static void requireValueType(
        String actual,
        String expected
    ) throws BadRequest {
        if (!expected.equals(actual)) {
            throw new BadRequest();
        }
    }

    private static double parseFiniteDouble(String value)
        throws BadRequest {
        try {
            double parsed = Double.parseDouble(value);
            if (!Double.isFinite(parsed)) {
                throw new BadRequest();
            }
            return parsed;
        } catch (NumberFormatException error) {
            throw new BadRequest();
        }
    }

    private static FieldSelection selectFields(
        List<Field> fields,
        String encodedNames,
        int maximum
    ) throws BadRequest {
        List<String> names = splitList(encodedNames, maximum);
        Map<String, Field> byName = indexFields(fields);
        int[] ids = new int[names.size()];
        for (int index = 0; index < names.size(); index++) {
            Field field = resolveField(
                byName,
                names.get(index).toLowerCase(Locale.ROOT)
            );
            ids[index] = field.getFieldID();
            names.set(index, field.getName().strip());
        }
        return new FieldSelection(names, ids);
    }

    private static List<SortInfo> parseSort(
        String encodedSort,
        List<Field> fields
    ) throws BadRequest {
        if (encodedSort.isBlank()) {
            return List.of();
        }
        List<String> parts = splitList(encodedSort, MAX_SORT_FIELDS);
        Map<String, Field> byName = indexFields(fields);
        List<SortInfo> result = new ArrayList<>();
        for (String part : parts) {
            int separator = part.lastIndexOf('.');
            if (separator < 1 || separator == part.length() - 1) {
                throw new BadRequest();
            }
            String name = part.substring(0, separator);
            String direction = part.substring(separator + 1);
            Field field = resolveField(
                byName,
                name.toLowerCase(Locale.ROOT)
            );
            int order;
            if ("asc".equals(direction)) {
                order = Constants.AR_SORT_ASCENDING;
            } else if ("desc".equals(direction)) {
                order = Constants.AR_SORT_DESCENDING;
            } else {
                throw new BadRequest();
            }
            result.add(new SortInfo(field.getFieldID(), order));
        }
        return result;
    }

    private static QualifierInfo matchAllQualification(
        ARServerUser user,
        String form,
        List<Field> fields
    ) throws ARException, BadRequest {
        Field requestId = null;
        for (Field field : fields) {
            if (field.getFieldID() == 1) {
                requestId = field;
                break;
            }
        }
        if (requestId == null) {
            throw new BadRequest();
        }
        String fieldName = requestId.getName()
            .replace("\\", "\\\\")
            .replace("'", "\\'");
        return user.parseQualification(
            form,
            "'" + fieldName + "' != $NULL$"
        );
    }

    private static Map<String, Field> indexFields(List<Field> fields) {
        Map<String, Field> byName = new HashMap<>();
        for (Field field : fields) {
            String canonical = field.getName()
                .strip()
                .toLowerCase(Locale.ROOT);
            if (byName.containsKey(canonical)) {
                byName.put(canonical, null);
            } else {
                byName.put(canonical, field);
            }
        }
        return byName;
    }

    private static Field resolveField(
        Map<String, Field> byName,
        String canonical
    ) throws BadRequest {
        if (!byName.containsKey(canonical)) {
            throw new BadRequest();
        }
        Field field = byName.get(canonical);
        if (field == null) {
            throw new AmbiguousField();
        }
        return field;
    }

    private static List<String> splitList(String value, int maximum)
        throws BadRequest {
        if (value.isBlank()) {
            throw new BadRequest();
        }
        String[] rawParts = value.split(",", -1);
        if (rawParts.length > maximum) {
            throw new BadRequest();
        }
        List<String> parts = new ArrayList<>();
        Map<String, Boolean> seen = new HashMap<>();
        for (String raw : rawParts) {
            String part = validateText(raw, MAX_NAME_LENGTH);
            String canonical = part.toLowerCase(Locale.ROOT);
            if (seen.putIfAbsent(canonical, Boolean.TRUE) != null) {
                throw new BadRequest();
            }
            parts.add(part);
        }
        return parts;
    }

    private static Map<String, String> readFormBody(HttpExchange exchange)
        throws IOException, BadRequest {
        String contentType = exchange.getRequestHeaders().getFirst(
            "Content-Type"
        );
        if (
            contentType == null
            || !contentType.toLowerCase(Locale.ROOT).startsWith(
                "application/x-www-form-urlencoded"
            )
        ) {
            throw new BadRequest();
        }
        byte[] body = exchange.getRequestBody().readNBytes(
            MAX_BODY_BYTES + 1
        );
        if (body.length > MAX_BODY_BYTES) {
            throw new BadRequest();
        }
        String encoded = new String(body, StandardCharsets.UTF_8);
        Map<String, String> values = new HashMap<>();
        for (String pair : encoded.split("&")) {
            if (pair.isEmpty()) {
                continue;
            }
            int separator = pair.indexOf('=');
            if (separator < 1) {
                throw new BadRequest();
            }
            String key = decode(pair.substring(0, separator));
            String value = decode(pair.substring(separator + 1));
            if (values.putIfAbsent(key, value) != null) {
                throw new BadRequest();
            }
        }
        return values;
    }

    private static String required(Map<String, String> values, String key)
        throws BadRequest {
        String value = values.get(key);
        if (value == null || value.isBlank()) {
            throw new BadRequest();
        }
        if (
            "host".equals(key)
            && !"127.0.0.1".equals(value)
            && !"::1".equals(value)
            && !"localhost".equals(value)
        ) {
            throw new BadRequest();
        }
        return value;
    }

    private static String requiredText(
        Map<String, String> values,
        String key,
        int maximumLength
    ) throws BadRequest {
        String value = values.get(key);
        if (value == null) {
            throw new BadRequest();
        }
        return validateText(value, maximumLength);
    }

    private static String requiredRaw(
        Map<String, String> values,
        String key,
        int maximumLength
    ) throws BadRequest {
        String value = values.get(key);
        if (value == null || value.length() > maximumLength) {
            throw new BadRequest();
        }
        return value;
    }

    private static String requiredSql(
        Map<String, String> values,
        String key,
        int maximumLength
    ) throws BadRequest {
        String value = values.get(key);
        if (value == null) {
            throw new BadRequest();
        }
        String stripped = value.strip();
        if (stripped.isEmpty() || stripped.length() > maximumLength) {
            throw new BadRequest();
        }
        for (int index = 0; index < stripped.length(); index++) {
            char character = stripped.charAt(index);
            if (
                (character < 0x20
                    && character != '\n'
                    && character != '\r'
                    && character != '\t')
                || character == 0x7f
            ) {
                throw new BadRequest();
            }
        }
        return stripped;
    }

    private static boolean isReadOnlySql(String sql) {
        if (
            sql.indexOf(';') >= 0
            || sql.indexOf('$') >= 0
            || sql.contains("--")
            || sql.contains("/*")
            || sql.contains("*/")
        ) {
            return false;
        }
        List<String> tokens = sqlTokens(sql);
        if (tokens.isEmpty()) {
            return false;
        }
        String first = tokens.get(0);
        if (!"select".equals(first) && !"with".equals(first)) {
            return false;
        }
        List<String> forbidden = List.of(
            "alter",
            "analyze",
            "call",
            "comment",
            "copy",
            "create",
            "delete",
            "do",
            "drop",
            "execute",
            "grant",
            "insert",
            "into",
            "lock",
            "merge",
            "refresh",
            "reindex",
            "revoke",
            "set",
            "truncate",
            "update",
            "vacuum"
        );
        for (String token : tokens) {
            if (forbidden.contains(token)) {
                return false;
            }
        }
        return true;
    }

    private static List<String> sqlTokens(String sql) {
        List<String> tokens = new ArrayList<>();
        StringBuilder token = new StringBuilder();
        boolean singleQuoted = false;
        boolean doubleQuoted = false;
        for (int index = 0; index < sql.length(); index++) {
            char character = sql.charAt(index);
            if (singleQuoted) {
                if (character == '\'' && index + 1 < sql.length()
                    && sql.charAt(index + 1) == '\'') {
                    index++;
                } else if (character == '\'') {
                    singleQuoted = false;
                }
                continue;
            }
            if (doubleQuoted) {
                if (character == '"' && index + 1 < sql.length()
                    && sql.charAt(index + 1) == '"') {
                    index++;
                } else if (character == '"') {
                    doubleQuoted = false;
                }
                continue;
            }
            if (character == '\'') {
                flushSqlToken(tokens, token);
                singleQuoted = true;
            } else if (character == '"') {
                flushSqlToken(tokens, token);
                doubleQuoted = true;
            } else if (Character.isLetterOrDigit(character)
                || character == '_') {
                token.append(Character.toLowerCase(character));
            } else {
                flushSqlToken(tokens, token);
            }
        }
        flushSqlToken(tokens, token);
        if (singleQuoted || doubleQuoted) {
            return List.of();
        }
        return tokens;
    }

    private static void flushSqlToken(
        List<String> tokens,
        StringBuilder token
    ) {
        if (token.length() > 0) {
            tokens.add(token.toString());
            token.setLength(0);
        }
    }

    private static String optionalText(
        Map<String, String> values,
        String key,
        int maximumLength
    ) throws BadRequest {
        String value = values.getOrDefault(key, "");
        if (value.isBlank()) {
            return "";
        }
        return validateText(value, maximumLength);
    }

    private static String validateText(String value, int maximumLength)
        throws BadRequest {
        String stripped = value.strip();
        if (
            stripped.isEmpty()
            || stripped.length() > maximumLength
            || containsControlCharacters(stripped)
        ) {
            throw new BadRequest();
        }
        return stripped;
    }

    private static boolean containsControlCharacters(String value) {
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (character < 0x20 || character == 0x7f) {
                return true;
            }
        }
        return false;
    }

    private static int parsePort(String value) throws BadRequest {
        return parseInteger(value, 1, 65_535);
    }

    private static int boundedInteger(
        Map<String, String> values,
        String key,
        int minimum,
        int maximum
    ) throws BadRequest {
        String value = values.get(key);
        if (value == null) {
            throw new BadRequest();
        }
        return parseInteger(value, minimum, maximum);
    }

    private static long boundedLong(
        Map<String, String> values,
        String key,
        long minimum,
        long maximum
    ) throws BadRequest {
        String value = values.get(key);
        if (value == null) {
            throw new BadRequest();
        }
        return parseLong(value, minimum, maximum);
    }

    private static int parseInteger(
        String value,
        int minimum,
        int maximum
    ) throws BadRequest {
        try {
            int parsed = Integer.parseInt(value);
            if (parsed < minimum || parsed > maximum) {
                throw new BadRequest();
            }
            return parsed;
        } catch (NumberFormatException error) {
            throw new BadRequest();
        }
    }

    private static long parseLong(
        String value,
        long minimum,
        long maximum
    ) throws BadRequest {
        try {
            long parsed = Long.parseLong(value);
            if (parsed < minimum || parsed > maximum) {
                throw new BadRequest();
            }
            return parsed;
        } catch (NumberFormatException error) {
            throw new BadRequest();
        }
    }

    private static boolean parseBoolean(String value) throws BadRequest {
        if ("true".equals(value)) {
            return true;
        }
        if ("false".equals(value)) {
            return false;
        }
        throw new BadRequest();
    }

    private static String decode(String value) throws BadRequest {
        try {
            return URLDecoder.decode(value, StandardCharsets.UTF_8);
        } catch (IllegalArgumentException error) {
            throw new BadRequest();
        }
    }

    private static String encodeForms(List<String> forms) {
        StringBuilder output = new StringBuilder();
        output.append("{\"forms\":[");
        for (int index = 0; index < forms.size(); index++) {
            if (index > 0) {
                output.append(',');
            }
            appendJsonString(output, forms.get(index));
        }
        output.append("],\"total\":").append(forms.size()).append('}');
        return output.toString();
    }

    private static String encodeSqlRows(
        List<List<Value>> rows,
        int limit,
        int columnCount
    ) {
        boolean truncated = rows.size() > limit;
        int returned = Math.min(rows.size(), limit);
        StringBuilder output = new StringBuilder();
        output.append("{\"rows\":[");
        for (int rowIndex = 0; rowIndex < returned; rowIndex++) {
            if (rowIndex > 0) {
                output.append(',');
            }
            List<Value> row = rows.get(rowIndex);
            if (row == null || row.size() != columnCount) {
                throw new IllegalStateException(
                    "ARAPI returned an unexpected SQL row shape"
                );
            }
            output.append('[');
            for (int columnIndex = 0; columnIndex < row.size(); columnIndex++) {
                if (columnIndex > 0) {
                    output.append(',');
                }
                Value value = row.get(columnIndex);
                appendJsonValue(
                    output,
                    value == null ? null : value.getValue()
                );
            }
            output.append(']');
        }
        output.append("],\"truncated\":").append(truncated).append('}');
        return output.toString();
    }

    private static String encodeArapiError(ARException error) {
        StringBuilder output = new StringBuilder();
        output.append("{\"error\":\"ARAPI operation failed\",\"codes\":[");
        List<StatusInfo> statuses = error.getLastStatus();
        if (statuses != null) {
            for (int index = 0; index < statuses.size(); index++) {
                if (index > 0) {
                    output.append(',');
                }
                output.append(statuses.get(index).getMessageNum());
            }
        }
        output.append("]}");
        return output.toString();
    }

    private static boolean hasArapiError(
        ARException error,
        int expected
    ) {
        List<StatusInfo> statuses = error.getLastStatus();
        if (statuses == null) {
            return false;
        }
        for (StatusInfo status : statuses) {
            if (status.getMessageNum() == expected) {
                return true;
            }
        }
        return false;
    }

    private static String encodeFields(List<Field> fields) {
        StringBuilder output = new StringBuilder();
        output.append("{\"fields\":[");
        for (int index = 0; index < fields.size(); index++) {
            if (index > 0) {
                output.append(',');
            }
            Field field = fields.get(index);
            output.append("{\"id\":").append(field.getFieldID());
            output.append(",\"name\":");
            appendJsonString(output, field.getName().strip());
            output.append(",\"datatype\":");
            appendJsonString(output, dataTypeName(field.getDataType()));
            output.append('}');
        }
        output.append("],\"total\":").append(fields.size()).append('}');
        return output.toString();
    }

    private static String dataTypeName(int value) {
        DataType datatype = DataType.toDataType(value);
        if (DataType.NULL.equals(datatype)) {
            return "NULL";
        }
        if (DataType.KEYWORD.equals(datatype)) {
            return "KEYWORD";
        }
        if (DataType.INTEGER.equals(datatype)) {
            return "INTEGER";
        }
        if (DataType.REAL.equals(datatype)) {
            return "REAL";
        }
        if (DataType.CHAR.equals(datatype)) {
            return "CHAR";
        }
        if (DataType.DIARY.equals(datatype)) {
            return "DIARY";
        }
        if (DataType.ENUM.equals(datatype)) {
            return "ENUM";
        }
        if (DataType.TIME.equals(datatype)) {
            return "TIME";
        }
        if (DataType.STATUS_HISTORY.equals(datatype)) {
            return "STATUS_HISTORY";
        }
        if (DataType.BITMASK.equals(datatype)) {
            return "BITMASK";
        }
        if (DataType.BYTES.equals(datatype)) {
            return "BYTES";
        }
        if (DataType.DECIMAL.equals(datatype)) {
            return "DECIMAL";
        }
        if (DataType.ATTACHMENT.equals(datatype)) {
            return "ATTACHMENT";
        }
        if (DataType.CURRENCY.equals(datatype)) {
            return "CURRENCY";
        }
        if (DataType.DATE.equals(datatype)) {
            return "DATE";
        }
        if (DataType.TIME_OF_DAY.equals(datatype)) {
            return "TIME_OF_DAY";
        }
        if (DataType.JOIN.equals(datatype)) {
            return "JOIN";
        }
        if (DataType.TRIM.equals(datatype)) {
            return "TRIM";
        }
        if (DataType.CONTROL.equals(datatype)) {
            return "CONTROL";
        }
        if (DataType.TABLE.equals(datatype)) {
            return "TABLE";
        }
        if (DataType.COLUMN.equals(datatype)) {
            return "COLUMN";
        }
        if (DataType.PAGE.equals(datatype)) {
            return "PAGE";
        }
        if (DataType.PAGE_HOLDER.equals(datatype)) {
            return "PAGE_HOLDER";
        }
        if (DataType.ATTACHMENT_POOL.equals(datatype)) {
            return "ATTACHMENT_POOL";
        }
        if (DataType.ULONG.equals(datatype)) {
            return "ULONG";
        }
        if (DataType.COORDS.equals(datatype)) {
            return "COORDS";
        }
        if (DataType.VIEW.equals(datatype)) {
            return "VIEW";
        }
        if (DataType.DISPLAY.equals(datatype)) {
            return "DISPLAY";
        }
        if (DataType.VALUELIST.equals(datatype)) {
            return "VALUELIST";
        }
        return "UNKNOWN_" + value;
    }

    private static String encodeEntries(
        List<Entry> entries,
        FieldSelection selected,
        int offset,
        int limit,
        Long total
    ) {
        StringBuilder output = new StringBuilder();
        output.append("{\"entries\":[");
        for (int index = 0; index < entries.size(); index++) {
            if (index > 0) {
                output.append(',');
            }
            appendEntry(output, entries.get(index), selected);
        }
        output.append("],\"offset\":").append(offset);
        output.append(",\"limit\":").append(limit);
        if (total != null) {
            output.append(",\"total\":").append(total);
        }
        output.append('}');
        return output.toString();
    }

    private static String encodeEntryResult(
        String entryId,
        Entry entry,
        FieldSelection selected
    ) {
        StringBuilder output = new StringBuilder();
        output.append("{\"entry_id\":");
        appendJsonString(output, entryId);
        output.append(",\"entry\":");
        appendEntry(output, entry, selected);
        output.append('}');
        return output.toString();
    }

    private static String encodePreparedEntry(
        String entryId,
        Entry entry,
        FieldSelection selected,
        long precondition
    ) {
        StringBuilder output = new StringBuilder();
        output.append("{\"entry_id\":");
        appendJsonString(output, entryId);
        output.append(",\"entry\":");
        appendEntry(output, entry, selected);
        output.append(",\"precondition\":");
        appendJsonString(output, Long.toString(precondition));
        output.append('}');
        return output.toString();
    }

    private static String encodeWriteResult(String entryId)
        throws BadRequest {
        String validated = validateText(entryId, MAX_NAME_LENGTH);
        StringBuilder output = new StringBuilder();
        output.append("{\"entry_id\":");
        appendJsonString(output, validated);
        output.append('}');
        return output.toString();
    }

    private static String encodeCreateResult(String entryId) {
        StringBuilder output = new StringBuilder();
        output.append("{\"entry_id\":");
        if (entryId == null) {
            output.append("null");
        } else {
            String stripped = entryId.strip();
            if (
                stripped.isEmpty()
                || stripped.length() > MAX_NAME_LENGTH
                || containsControlCharacters(stripped)
            ) {
                output.append("null");
            } else {
                appendJsonString(output, stripped);
            }
        }
        output.append('}');
        return output.toString();
    }

    private static void appendEntry(
        StringBuilder output,
        Entry entry,
        FieldSelection selected
    ) {
        output.append("{\"values\":{");
        for (int index = 0; index < selected.names.size(); index++) {
            if (index > 0) {
                output.append(',');
            }
            appendJsonString(output, selected.names.get(index));
            output.append(':');
            Value value = entry.get(selected.ids[index]);
            appendJsonValue(output, value == null ? null : value.getValue());
        }
        output.append("}}");
    }

    private static void appendJsonValue(
        StringBuilder output,
        Object value
    ) {
        if (value == null) {
            output.append("null");
        } else if (value instanceof Boolean) {
            output.append(value);
        } else if (
            value instanceof Byte
            || value instanceof Short
            || value instanceof Integer
            || value instanceof Long
            || value instanceof BigInteger
            || value instanceof BigDecimal
        ) {
            output.append(value);
        } else if (value instanceof Float number) {
            if (Float.isFinite(number)) {
                output.append(number);
            } else {
                appendJsonString(output, number.toString());
            }
        } else if (value instanceof Double number) {
            if (Double.isFinite(number)) {
                output.append(number);
            } else {
                appendJsonString(output, number.toString());
            }
        } else {
            appendJsonString(output, String.valueOf(value));
        }
    }

    private static void appendJsonString(StringBuilder output, String value) {
        output.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"' -> output.append("\\\"");
                case '\\' -> output.append("\\\\");
                case '\b' -> output.append("\\b");
                case '\f' -> output.append("\\f");
                case '\n' -> output.append("\\n");
                case '\r' -> output.append("\\r");
                case '\t' -> output.append("\\t");
                default -> {
                    if (character < 0x20) {
                        output.append(
                            String.format("\\u%04x", (int) character)
                        );
                    } else {
                        output.append(character);
                    }
                }
            }
        }
        output.append('"');
    }

    private static void respond(
        HttpExchange exchange,
        int status,
        String payload
    ) throws IOException {
        byte[] body = payload.getBytes(StandardCharsets.UTF_8);
        if (body.length > MAX_RESPONSE_BYTES) {
            status = 502;
            body = (
                "{\"error\":\"bridge response exceeds the safety limit\"}"
            ).getBytes(StandardCharsets.UTF_8);
        }
        exchange.getResponseHeaders().set(
            "Content-Type",
            "application/json; charset=utf-8"
        );
        exchange.getResponseHeaders().set("Cache-Control", "no-store");
        exchange.sendResponseHeaders(status, body.length);
        try (var stream = exchange.getResponseBody()) {
            stream.write(body);
        }
    }

    @FunctionalInterface
    private interface UserOperation<T> {
        T execute(ARServerUser user)
            throws ARException, BadRequest, AdminRequired;
    }

    private static final class FieldSelection {
        private final List<String> names;
        private final int[] ids;

        FieldSelection(List<String> names, int[] ids) {
            this.names = names;
            this.ids = ids;
        }
    }

    private static class BadRequest extends Exception {
        private static final long serialVersionUID = 1L;
    }

    private static final class AmbiguousField extends BadRequest {
        private static final long serialVersionUID = 1L;
    }

    private static final class AdminRequired extends Exception {
        private static final long serialVersionUID = 1L;
    }

    private static final class Conflict extends RuntimeException {
        private static final long serialVersionUID = 1L;
    }
}
