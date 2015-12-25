class OverridableSettings(object):
    """
    First tries to return overrides.attr, if fails. returns defaults.attr.
    """
    def __init__(self, defaults, overrides):
        self._defaults = defaults
        self._overrides = overrides

    def __getattr__(self, item):
        if item in ("_overrides", "_defaults"):
            return super(OverridableSettings, self).__getattr__(item)
        return self._overrides.get(item, self._defaults[item])

    def keys(self):
        return set.union(set(self._defaults), set(self._overrides))

    def __iter__(self):
        return iter(self.keys())

    def __getitem__(self, item):
        return getattr(self, item)
