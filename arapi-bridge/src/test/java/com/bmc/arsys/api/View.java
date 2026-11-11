package com.bmc.arsys.api;

public final class View {
    private final String form;
    private final int id;
    public View(String form, int id) { this.form = form; this.id = id; }
    public String getFormName() { return form; }
    public int getVUIId() { return id; }
    public String getName() { return id == 100 ? "Standard" : "Progressive"; }
    public String getLocale() { return "en_US"; }
    public int getVUIType() { return id == 100 ? 1 : 9; }
    public DisplayPropertyMap getDisplayProperties() {
        DisplayPropertyMap properties = new DisplayPropertyMap();
        properties.put(Constants.AR_DPROP_VIEW_RTL, new Value(0));
        properties.put(999999, new Value("PRIVATE-UNSUPPORTED-PROPERTY"));
        return properties;
    }
}
