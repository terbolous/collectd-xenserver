collectd-xenserver
==================

A Collectd plugin to monitor Citrix XenServer

# Introduction

This is a module for collectd. It try to fetch the last metrics from a Citrix Xenserver
host and the VMs running on it. This is done by fetching and parsing a xml on the server:

The module automatically detects if a host is pool master or not, and supports
multiple pools.

http://$username:$password@$host/rrd_updates?start=<secondssinceepoch>&host=true

For more informations about this API, see the Citrix documentation here:

http://docs.vmd.citrix.com/XenServer/6.1.0/1.0/en_gb/sdk.html#persistent_perf_stats


# Dependencies

* Collectd 4.9 or later (for the Python plugin)
* Python 2.4 or later
* XenAPI python module: http://pypi.python.org/pypi/XenAPI
* collectd python module: http://pypi.python.org/pypi/collectd


# Configuration

The plugin has some mandatory configuration options. This is done by passing parameters via the <Module> config section in your Collectd config. The following parameters are recognized:

* Host - IP address of the XenServer (Note: currently only IP address is supported due to cluster support)
* User - the username for authentication
* Password - the password for authentication
* Cluster - where to group the graphs from this host (optional)

```
  <LoadPlugin python>
    Globals true
  </LoadPlugin>

  <Plugin python>
        ModulePath "/path/to/modules/"
        LogTraces true
        Interactive false
        Import "collectd-xenserver"
        <Module "collectd-xenserver">
              <Host "10.0.0.100">
                    User "root"
                    Password "mysecretpassword"
                    Cluster "mycluster"
              </Host>
              <Host "10.0.0.101">
                    User "root"
                    Password "mysecretpassword"
                    Cluster "anothercluster.subcluster"
              </Host>
        </Module>
  </Plugin>
```

XenApi/rrd_updates
------------------
http://wiki.xensource.com/xenwiki/XAPI_RRDs
http://community.citrix.com/display/xs/Using+XenServer+RRDs
