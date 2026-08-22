"""Filter wheel controller for INDI devices."""


class FilterWheelController:
    """Manages filter wheel operations via INDI."""

    def __init__(self, client):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.client = client

    def get_names(self, device) -> list[str]:  # ruff: ignore[missing-type-function-argument]
        """Return a list of available filter names from the filter wheel.

        Returns
        -------
        names : `list` [`str`]
            The configured filter names, or an empty list if the
            device has no ``FILTER_NAME`` property.
        """
        if not device:
            return []
        names_property = device.getText("FILTER_NAME")
        if names_property:
            return [name_element.getText() for name_element in names_property]
        return []

    def resolve_name(self, device, filter_name: str) -> str | None:  # ruff: ignore[missing-type-function-argument]
        """Resolve a target filter name to the name supported by the device.

        Uses exact and prefix/fuzzy matches to bridge telescope control
        requests.

        Parameters
        ----------
        device
            INDI device handle for the filter wheel.
        filter_name : str
            Requested filter name to resolve.

        Returns
        -------
        Optional[str]
            The matched device filter name, the original filter_name if
            the device has no known filters, or None if no match is found.
        """
        known_filters = self.get_names(device)
        if not known_filters:
            return filter_name

        target_filter_lower = filter_name.lower()
        for known_filter in known_filters:
            if not known_filter:
                continue
            known_filter_lower = known_filter.lower()
            if known_filter_lower == target_filter_lower:
                return known_filter
            if (
                len(filter_name) == 1
                and known_filter_lower.startswith(target_filter_lower)
                and target_filter_lower in ["r", "g", "b", "l"]
            ):
                return known_filter
            if target_filter_lower == "spect" and "spec" in known_filter_lower:
                return known_filter
            if known_filter_lower == "spect" and "spec" in target_filter_lower:
                return known_filter
        return None

    def set_position(self, device, filter_name) -> bool:  # ruff: ignore[missing-type-function-argument]
        """Set the active filter on the filter wheel by slot number or name.

        Handles fuzzy matching for standard filters.

        Parameters
        ----------
        device
            INDI device handle for the filter wheel.
        filter_name
            Target filter name, or a numeric slot index as a string.

        Returns
        -------
        bool
            True if the filter position was set, False otherwise.
        """
        if not device:
            return False

        # 1. Try Standard (Number Property + Name Lookup)
        slot_property = device.getNumber("FILTER_SLOT")
        if slot_property:
            names_property = device.getText("FILTER_NAME")
            if names_property:
                target_name = filter_name.lower()
                for i in range(len(names_property)):
                    current_name = names_property[i].getText().lower()

                    match = False
                    if current_name == target_name:
                        match = True
                    elif len(target_name) == 1 and current_name.startswith(target_name):
                        if target_name in ["r", "g", "b", "l"]:
                            match = True
                    elif target_name == "spect" and "spec" in current_name:
                        match = True
                    elif "spec" in target_name and current_name == "spect":
                        match = True

                    if match:
                        # Slot is usually 1-indexed to match the name index.
                        slot_property[0].value = i + 1
                        self.client.sendNewNumber(slot_property)
                        return True

            # If name not found, try parsing the request as an integer index.
            try:
                slot_index = int(filter_name)
                slot_property[0].value = slot_index
                self.client.sendNewNumber(slot_property)
                return True
            except ValueError:
                pass

        # 2. Try Legacy/Text (Text Property)
        filter_property = device.getText("FILTER_SLOT")
        if filter_property:
            filter_property[0].text = filter_name
            self.client.sendNewText(filter_property)
            return True

        return False

    def get_current_filter(self, device) -> str | None:  # ruff: ignore[missing-type-function-argument]
        """Return the current filter name, or None if unavailable.

        Falls back to the slot number as a string if no name is known.

        Returns
        -------
        filter_name : `str` or `None`
            The current filter name (or slot index as a string), or
            `None` if the device or slot property is unavailable.
        """
        if not device:
            return None

        slot_property = device.getNumber("FILTER_SLOT")
        if not slot_property:
            text_property = device.getText("FILTER_SLOT")
            if text_property:
                return text_property[0].text
            return None

        current_slot_index = int(slot_property[0].value)  # 1-based index usually
        names_property = device.getText("FILTER_NAME")
        if names_property and 0 < current_slot_index <= len(names_property):
            return names_property[current_slot_index - 1].getText()
        return str(current_slot_index)
