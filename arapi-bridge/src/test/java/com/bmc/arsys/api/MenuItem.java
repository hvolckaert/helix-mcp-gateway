package com.bmc.arsys.api;

import java.util.List;

public final class MenuItem {
    private final String label;
    private final Object content;

    public MenuItem(String label, String value) {
        this.label = label;
        this.content = value;
    }

    public MenuItem(String label, List<MenuItem> submenu) {
        this.label = label;
        this.content = submenu;
    }

    public int getType() {
        return content instanceof List<?>
            ? Constants.AR_MENU_TYPE_MENU
            : Constants.AR_MENU_TYPE_VALUE;
    }

    public String getLabel() {
        return label;
    }

    @SuppressWarnings("unchecked")
    public List<MenuItem> getSubMenu() {
        return content instanceof List<?>
            ? (List<MenuItem>) content
            : null;
    }

    public String getValue() {
        return content instanceof String ? (String) content : null;
    }
}
