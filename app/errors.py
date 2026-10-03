"""Exceptions raised by the model builder."""


class BuildError(Exception):
    """A build could not be completed. The message is meant for the CLI user."""


class BundleError(BuildError):
    """The model ZIP is invalid, unsafe, or exceeds the configured limits."""
