package com.example.helix.bridge;

import com.bmc.arsys.api.ARException;
import com.bmc.arsys.api.ARServerUser;
import com.bmc.arsys.api.Constants;
import com.bmc.arsys.api.DataType;
import com.bmc.arsys.api.Entry;
import com.bmc.arsys.api.Field;
import com.bmc.arsys.api.ServerInfoMap;
import com.bmc.arsys.api.StatusInfo;
import com.bmc.arsys.api.Timestamp;
import com.bmc.arsys.api.Value;
import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpContext;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpPrincipal;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.math.BigDecimal;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Arrays;
import java.util.List;
import java.util.Map;

public final class ArapiBridgeTest {
    private static int completed;

    private ArapiBridgeTest() {
    }

    public static void main(String[] args) throws Exception {
        run("create result encoding", ArapiBridgeTest::testCreateResult);
        run("JSON encoding", ArapiBridgeTest::testJsonEncoding);
        run("bounded scalar parsing", ArapiBridgeTest::testScalarParsing);
        run("list and host validation", ArapiBridgeTest::testValidation);
        run(
            "duplicate field names",
            ArapiBridgeTest::testDuplicateFieldNames
        );
        run("typed write values", ArapiBridgeTest::testTypedWriteValues);
        run("modified date handling", ArapiBridgeTest::testModifiedDate);
        run("conditional update", ArapiBridgeTest::testConditionalUpdate);
        run("session cleanup", ArapiBridgeTest::testSessionCleanup);
        run("HTTP protocol", ArapiBridgeTest::testHttpProtocol);
        run("SQL validation and encoding", ArapiBridgeTest::testSql);
        run("ARAPI error sanitization", ArapiBridgeTest::testArapiError);
        System.out.println("ARAPI bridge tests: " + completed + " passed");
    }

    private static void testCreateResult() throws Exception {
        assertEquals(
            "{\"entry_id\":\"0001\"}",
            invoke("encodeCreateResult", types(String.class), " 0001 ")
        );
        assertEquals(
            "{\"entry_id\":null}",
            invoke("encodeCreateResult", types(String.class), (Object) null)
        );
        assertEquals(
            "{\"entry_id\":null}",
            invoke("encodeCreateResult", types(String.class), "bad\nvalue")
        );
    }

    private static void testJsonEncoding() throws Exception {
        assertEquals(
            "{\"forms\":[\"A\",\"B\\\"C\"],\"total\":2}",
            invoke("encodeForms", types(List.class), List.of("A", "B\"C"))
        );
        StringBuilder output = new StringBuilder();
        invoke(
            "appendJsonValue",
            types(StringBuilder.class, Object.class),
            output,
            Double.NaN
        );
        assertEquals("\"NaN\"", output.toString());
    }

    private static void testScalarParsing() throws Exception {
        assertEquals(true, invoke("parseBoolean", types(String.class), "true"));
        assertEquals(false, invoke("parseBoolean", types(String.class), "false"));
        assertFailure("BadRequest", "parseBoolean", types(String.class), "TRUE");
        assertEquals(
            65535,
            invoke("parsePort", types(String.class), "65535")
        );
        assertFailure("BadRequest", "parsePort", types(String.class), "0");
        assertFailure("BadRequest", "parsePort", types(String.class), "x");
    }

    private static void testValidation() throws Exception {
        assertEquals(
            List.of("Field", "Other"),
            invoke("splitList", types(String.class, int.class), " Field,Other ", 2)
        );
        assertFailure(
            "BadRequest",
            "splitList",
            types(String.class, int.class),
            "Field,field",
            2
        );
        assertFailure(
            "BadRequest",
            "splitList",
            types(String.class, int.class),
            "Field,Other",
            1
        );
        Map<String, String> input = new HashMap<>();
        input.put("host", "127.0.0.1");
        assertEquals(
            "127.0.0.1",
            invoke("required", types(Map.class, String.class), input, "host")
        );
        input.put("host", "remote.example");
        assertFailure(
            "BadRequest",
            "required",
            types(Map.class, String.class),
            input,
            "host"
        );
    }

    private static void testDuplicateFieldNames() throws Exception {
        List<Field> fields = List.of(
            new Field(100, "Status", DataType.ENUM.getValue()),
            new Field(200, "Status", DataType.CHAR.getValue()),
            new Field(300, " InstanceId ", DataType.CHAR.getValue())
        );
        Object selected = invoke(
            "selectFields",
            types(List.class, String.class, int.class),
            fields,
            "InstanceId",
            1
        );
        if (selected == null) {
            throw new AssertionError("unique field was not selected");
        }
        assertEquals(
            "{\"fields\":[{\"id\":300,\"name\":\"InstanceId\","
                + "\"datatype\":\"CHAR\"}],\"total\":1}",
            invoke("encodeFields", types(List.class), List.of(fields.get(2)))
        );
        assertFailure(
            "AmbiguousField",
            "selectFields",
            types(List.class, String.class, int.class),
            fields,
            "Status",
            1
        );
        assertFailure(
            "AmbiguousField",
            "parseSort",
            types(String.class, List.class),
            "Status.asc",
            fields
        );
        Map<String, String> values = Map.of(
            "value_count", "1",
            "field_0", "Status",
            "value_type_0", "integer",
            "value_0", "1"
        );
        assertFailure(
            "AmbiguousField",
            "parseWriteValues",
            types(Map.class, List.class),
            values,
            fields
        );
    }

    private static void testTypedWriteValues() throws Exception {
        Value text = parseWriteValue(DataType.CHAR, "string", "hello");
        assertEquals("hello", text.getValue());

        Value flag = parseWriteValue(DataType.ENUM, "boolean", "true");
        assertEquals(1, flag.getValue());

        Value decimal = parseWriteValue(DataType.DECIMAL, "number", "12.50");
        assertEquals(new BigDecimal("12.50"), decimal.getValue());

        assertWriteFailure(DataType.REAL, "number", "NaN");
        assertWriteFailure(DataType.ULONG, "integer", "-1");
        assertWriteFailure(DataType.TIME_OF_DAY, "integer", "86400");
        assertWriteFailure(DataType.ATTACHMENT, "string", "blocked");
    }

    private static void testModifiedDate() throws Exception {
        int[] original = {1, 2};
        int[] extended = (int[]) invoke(
            "includeModifiedDate",
            types(int[].class),
            (Object) original
        );
        assertEquals(3, extended.length);
        assertEquals(Constants.AR_CORE_MODIFIED_DATE, extended[2]);

        int[] present = {1, Constants.AR_CORE_MODIFIED_DATE};
        Object unchanged = invoke(
            "includeModifiedDate",
            types(int[].class),
            (Object) present
        );
        assertSame(present, unchanged);

        Entry entry = entryWithModifiedDate(42);
        assertEquals(
            42L,
            invoke("readModifiedDate", types(Entry.class), entry)
        );
        assertFailure(
            "IllegalStateException",
            "readModifiedDate",
            types(Entry.class),
            new Entry()
        );
    }

    private static void testConditionalUpdate() throws Exception {
        Entry values = new Entry();
        values.put(100, new Value("new value"));

        ARServerUser stale = new ARServerUser();
        stale.setEntryForTest(entryWithModifiedDate(100));
        assertFailure(
            "Conflict",
            "conditionallyUpdate",
            types(
                ARServerUser.class,
                String.class,
                String.class,
                Entry.class,
                long.class
            ),
            stale,
            "Form",
            "1",
            values,
            99L
        );
        assertEquals(false, stale.isSetEntryCalled());

        ARServerUser current = new ARServerUser();
        current.setEntryForTest(entryWithModifiedDate(100));
        ServerInfoMap serverInfo = new ServerInfoMap();
        serverInfo.put(
            Constants.AR_SERVER_INFO_SERVER_TIME,
            new Value(new Timestamp(90))
        );
        current.setServerInfoForTest(serverInfo);
        invoke(
            "conditionallyUpdate",
            types(
                ARServerUser.class,
                String.class,
                String.class,
                Entry.class,
                long.class
            ),
            current,
            "Form",
            "1",
            values,
            100L
        );
        assertEquals(true, current.isSetEntryCalled());
        assertEquals(101L, current.getLastGetTime().getValue());
    }

    private static void testSessionCleanup() throws Exception {
        Class<?> operationType = Class.forName(
            "com.example.helix.bridge.ArapiBridge$UserOperation"
        );
        Object operation = Proxy.newProxyInstance(
            ArapiBridgeTest.class.getClassLoader(),
            new Class<?>[] {operationType},
            (proxy, method, arguments) -> "ok"
        );
        Map<String, String> input = Map.of(
            "host", "localhost",
            "port", "46000",
            "username", "user",
            "password", "secret"
        );
        assertEquals(
            "ok",
            invoke("withUser", types(Map.class, operationType), input, operation)
        );
        ARServerUser user = ARServerUser.getLastInstance();
        assertEquals(true, user.isLoginCalled());
        assertEquals(true, user.isLogoutCalled());
        assertEquals(true, user.isClearCalled());
    }

    private static void testHttpProtocol() throws Exception {
        HttpHandler health = newHandler("HealthHandler");
        FakeExchange healthy = new FakeExchange("GET", "/health", "", null);
        health.handle(healthy);
        assertEquals(200, healthy.getResponseCode());
        assertEquals("{\"status\":\"ok\"}", healthy.responseText());
        assertEquals("no-store", healthy.getResponseHeaders().getFirst("Cache-Control"));

        FakeExchange wrongHealthMethod = new FakeExchange(
            "POST",
            "/health",
            "",
            null
        );
        health.handle(wrongHealthMethod);
        assertEquals(405, wrongHealthMethod.getResponseCode());

        HttpHandler forms = newHandler("FormsHandler");
        String credentials = (
            "host=localhost&port=46000&username=user&password=secret"
        );
        FakeExchange valid = new FakeExchange(
            "POST",
            "/v1/forms",
            credentials,
            "application/x-www-form-urlencoded; charset=utf-8"
        );
        forms.handle(valid);
        assertEquals(200, valid.getResponseCode());
        assertEquals("{\"forms\":[],\"total\":0}", valid.responseText());

        FakeExchange remoteHost = new FakeExchange(
            "POST",
            "/v1/forms",
            credentials.replace("localhost", "remote.example"),
            "application/x-www-form-urlencoded"
        );
        forms.handle(remoteHost);
        assertEquals(400, remoteHost.getResponseCode());
        assertEquals(
            "{\"error\":\"invalid request\"}",
            remoteHost.responseText()
        );

        FakeExchange missingContentType = new FakeExchange(
            "POST",
            "/v1/forms",
            credentials,
            null
        );
        forms.handle(missingContentType);
        assertEquals(400, missingContentType.getResponseCode());
    }

    private static void testSql() throws Exception {
        assertEquals(
            true,
            invoke("isReadOnlySql", types(String.class), "SELECT 1 AS value")
        );
        assertEquals(
            true,
            invoke(
                "isReadOnlySql",
                types(String.class),
                "WITH values AS (SELECT 1 AS value) SELECT value FROM values"
            )
        );
        assertEquals(
            false,
            invoke(
                "isReadOnlySql",
                types(String.class),
                "WITH changed AS (DELETE FROM sample RETURNING id) SELECT id FROM changed"
            )
        );
        assertEquals(
            false,
            invoke(
                "isReadOnlySql",
                types(String.class),
                "SELECT 1 AS value; DROP TABLE sample"
            )
        );
        assertEquals(
            "{\"rows\":[[1,\"two\",null]],\"truncated\":true}",
            invoke(
                "encodeSqlRows",
                types(List.class, int.class, int.class),
                List.of(
                    Arrays.asList(new Value(1), new Value("two"), null),
                    Arrays.asList(new Value(2), new Value("ignored"), null)
                ),
                1,
                3
            )
        );

        HttpHandler sql = newHandler("SqlQueryHandler");
        String credentials = (
            "host=localhost&port=46000&username=user&password=secret"
        );
        FakeExchange valid = new FakeExchange(
            "POST",
            "/v1/sql/query",
            credentials + "&sql=SELECT+1+AS+value&limit=2&column_count=1",
            "application/x-www-form-urlencoded"
        );
        sql.handle(valid);
        assertEquals(200, valid.getResponseCode());
        assertEquals(
            "{\"rows\":[],\"truncated\":false}",
            valid.responseText()
        );
        ARServerUser user = ARServerUser.getLastInstance();
        assertEquals("SELECT 1 AS value", user.getLastSql());
        assertEquals(3, user.getLastSqlLimit());
        assertEquals(false, user.isLastRetrieveNumMatches());

        ARServerUser.setAdministratorDefaultForTest(false);
        try {
            FakeExchange forbidden = new FakeExchange(
                "POST",
                "/v1/sql/query",
                credentials + "&sql=SELECT+1+AS+value&limit=2&column_count=1",
                "application/x-www-form-urlencoded"
            );
            sql.handle(forbidden);
            assertEquals(403, forbidden.getResponseCode());
            assertEquals(
                "{\"error\":\"ARAPI administrator permission required\","
                    + "\"code\":\"ARAPI_ADMIN_REQUIRED\"}",
                forbidden.responseText()
            );
        } finally {
            ARServerUser.setAdministratorDefaultForTest(true);
        }
    }

    private static void testArapiError() throws Exception {
        ARException error = new ARException(
            List.of(new StatusInfo(309), new StatusInfo(302))
        );
        assertEquals(
            "{\"error\":\"ARAPI operation failed\",\"codes\":[309,302]}",
            invoke("encodeArapiError", types(ARException.class), error)
        );
        assertEquals(
            true,
            invoke(
                "hasArapiError",
                types(ARException.class, int.class),
                error,
                309
            )
        );
    }

    private static Value parseWriteValue(
        DataType datatype,
        String valueType,
        String value
    ) throws Exception {
        Field field = new Field(100, "Field", datatype.getValue());
        return (Value) invoke(
            "parseWriteValue",
            types(Field.class, String.class, String.class),
            field,
            valueType,
            value
        );
    }

    private static void assertWriteFailure(
        DataType datatype,
        String valueType,
        String value
    ) throws Exception {
        Field field = new Field(100, "Field", datatype.getValue());
        assertFailure(
            "BadRequest",
            "parseWriteValue",
            types(Field.class, String.class, String.class),
            field,
            valueType,
            value
        );
    }

    private static Entry entryWithModifiedDate(long value) {
        Entry entry = new Entry();
        entry.put(
            Constants.AR_CORE_MODIFIED_DATE,
            new Value(new Timestamp(value))
        );
        return entry;
    }

    private static HttpHandler newHandler(String simpleName) throws Exception {
        Class<?> handlerType = Class.forName(
            "com.example.helix.bridge.ArapiBridge$" + simpleName
        );
        var constructor = handlerType.getDeclaredConstructor();
        constructor.setAccessible(true);
        return (HttpHandler) constructor.newInstance();
    }

    private static Object invoke(
        String name,
        Class<?>[] parameterTypes,
        Object... arguments
    ) throws Exception {
        Method method = ArapiBridge.class.getDeclaredMethod(name, parameterTypes);
        method.setAccessible(true);
        try {
            return method.invoke(null, arguments);
        } catch (InvocationTargetException error) {
            Throwable cause = error.getCause();
            if (cause instanceof Exception exception) {
                throw exception;
            }
            throw error;
        }
    }

    private static void assertFailure(
        String expected,
        String name,
        Class<?>[] parameterTypes,
        Object... arguments
    ) throws Exception {
        try {
            invoke(name, parameterTypes, arguments);
            throw new AssertionError("expected " + expected);
        } catch (Exception error) {
            if (!expected.equals(error.getClass().getSimpleName())) {
                throw error;
            }
        }
    }

    private static Class<?>[] types(Class<?>... values) {
        return values;
    }

    private static void assertEquals(Object expected, Object actual) {
        if (!expected.equals(actual)) {
            throw new AssertionError(
                "expected <" + expected + "> but was <" + actual + ">"
            );
        }
    }

    private static void assertSame(Object expected, Object actual) {
        if (expected != actual) {
            throw new AssertionError("expected both values to be identical");
        }
    }

    private static void run(String name, CheckedRunnable test) throws Exception {
        try {
            test.run();
            completed++;
            System.out.println("PASS " + name);
        } catch (Exception | AssertionError error) {
            System.err.println("FAIL " + name + ": " + error);
            throw error;
        }
    }

    @FunctionalInterface
    private interface CheckedRunnable {
        void run() throws Exception;
    }

    private static final class FakeExchange extends HttpExchange {
        private final Headers requestHeaders = new Headers();
        private final Headers responseHeaders = new Headers();
        private final String method;
        private final URI uri;
        private final InputStream requestBody;
        private final ByteArrayOutputStream responseBody =
            new ByteArrayOutputStream();
        private final Map<String, Object> attributes = new HashMap<>();
        private int responseCode = -1;

        FakeExchange(
            String method,
            String path,
            String body,
            String contentType
        ) {
            this.method = method;
            uri = URI.create(path);
            requestBody = new ByteArrayInputStream(
                body.getBytes(StandardCharsets.UTF_8)
            );
            if (contentType != null) {
                requestHeaders.set("Content-Type", contentType);
            }
        }

        String responseText() {
            return responseBody.toString(StandardCharsets.UTF_8);
        }

        @Override
        public Headers getRequestHeaders() {
            return requestHeaders;
        }

        @Override
        public Headers getResponseHeaders() {
            return responseHeaders;
        }

        @Override
        public URI getRequestURI() {
            return uri;
        }

        @Override
        public String getRequestMethod() {
            return method;
        }

        @Override
        public HttpContext getHttpContext() {
            return null;
        }

        @Override
        public void close() {
        }

        @Override
        public InputStream getRequestBody() {
            return requestBody;
        }

        @Override
        public OutputStream getResponseBody() {
            return responseBody;
        }

        @Override
        public void sendResponseHeaders(int code, long responseLength) {
            responseCode = code;
        }

        @Override
        public InetSocketAddress getRemoteAddress() {
            return new InetSocketAddress("127.0.0.1", 12345);
        }

        @Override
        public int getResponseCode() {
            return responseCode;
        }

        @Override
        public InetSocketAddress getLocalAddress() {
            return new InetSocketAddress("127.0.0.1", 8090);
        }

        @Override
        public String getProtocol() {
            return "HTTP/1.1";
        }

        @Override
        public Object getAttribute(String name) {
            return attributes.get(name);
        }

        @Override
        public void setAttribute(String name, Object value) {
            attributes.put(name, value);
        }

        @Override
        public void setStreams(InputStream input, OutputStream output) {
        }

        @Override
        public HttpPrincipal getPrincipal() {
            return null;
        }
    }
}
