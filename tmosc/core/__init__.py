"""Responsibility mixins composed by tmosc.bridge.TotalMixOSCBridge.

Each module holds one slice of the bridge's behaviour as a plain mixin class
(no __init__, no super() chain); the facade in tmosc/bridge.py owns every
instance attribute, the process lifecycle (start_*/stop_*) and the methods
that read env config. No module here imports tmosc.bridge.
"""
