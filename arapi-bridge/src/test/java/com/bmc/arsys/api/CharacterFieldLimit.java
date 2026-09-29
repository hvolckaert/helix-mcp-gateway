package com.bmc.arsys.api;

public final class CharacterFieldLimit extends FieldLimit {
    private final String charMenu;
    private final int menuStyle;

    public CharacterFieldLimit(String charMenu, int menuStyle) {
        this.charMenu = charMenu;
        this.menuStyle = menuStyle;
    }

    public String getCharMenu() {
        return charMenu;
    }

    public int getMenuStyle() {
        return menuStyle;
    }
}
