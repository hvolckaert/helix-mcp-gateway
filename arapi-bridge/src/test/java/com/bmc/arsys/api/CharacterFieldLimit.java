package com.bmc.arsys.api;

public final class CharacterFieldLimit extends FieldLimit {
    private final String charMenu;
    private final int menuStyle;

    public int getMaxLength() { return 255; }
    public int getLengthUnits() { return 1; }
    public String getPattern() { return "[A-Z]*"; }
    public int getStorageOptionForCLOB() { return 0; }
    public int getQBEMatch() { return 1; }
    public int getFullTextOption() { return 0; }

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
