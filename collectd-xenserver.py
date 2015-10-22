#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
This is a module for collectd. It try to fetch the last metrics from a Citrix Xenserver
host and the VMs running on it. This is done by fetching and parsing a xml on the server:

http://<username>:<password>@<host>/rrd_updates?start=<secondssinceepoch>&host=true

For more informations about this API, see the Citrix documentation here:

http://docs.vmd.citrix.com/XenServer/6.1.0/1.0/en_gb/sdk.html#persistent_perf_stats
http://wiki.xensource.com/xenwiki/XAPI_RRDs
http://community.citrix.com/display/xs/Using+XenServer+RRDs

Dependencies:
  - XenAPI python module: http://pypi.python.org/pypi/XenAPI
  - collectd python module: http://pypi.python.org/pypi/collectd

collectd.conf example:
  <Plugin python>
        ModulePath "/path/to/modules/"
        LogTraces true
        Interactive false
        Import "collectd-xenserver"
        <Module "collectd-xenserver">
              <Host "10.0.0.100">
                    User "root"
                    Password "mysecretpassword"
              </Host>
              <Host "10.0.0.101">>
                    User "root"
                    Password "mysecretpassword"
              </Host>
        </Module>
  </Plugin>

"""
__author__ = "Adrien Pujol - http://www.crashdump.fr/"
__copyright__ = "Copyright 2012, Adrien Pujol"
__license__ = "GPL"
__version__ = "0.3d"
__email__ = "adrien.pujol@crashdump.fr"
__status__ = "Development"

import XenAPI
import collectd
import urllib
import os, sys, time, getopt
from xml.dom import minidom
from xml.parsers.expat import ExpatError


# Per VM dictionary (used by GetRRDUpdates to look up column numbers by variable names)
class VMReport(dict):
    """Used internally by GetRRDUpdates"""
    def __init__(self, uuid):
        self.uuid = uuid

# Per Host dictionary (used by GetRRDUpdates to look up column numbers by variable names)
class HostReport(dict):
    """Used internally by GetRRDUpdates"""
    def __init__(self, uuid):
        self.uuid = uuid

# Fetch and parse data class
class GetRRDUpdates:
    """ Object used to get and parse the output the http://host/rrd_udpates?..."""
    def __init__(self):
        # rrdParams are what get passed to the CGI executable in the URL
        self.rrdParams = dict()
        self.rrdParams['start'] = int(time.time()) - 10
        self.rrdParams['host'] = 'true'   # include data for host (as well as for VMs)
        self.rrdParams['cf'] = 'AVERAGE'  # consolidation function, each sample averages 12 from the 5 second RRD
        self.rrdParams['interval'] = '10'

    def GetRows(self):
        return self.rows

    def GetVMList(self):
        return self.vm_reports.keys()

    def GetVMParamList(self, uuid):
        report = self.vm_reports[uuid]
        if not report:
            return []
        return report.keys()

    def GetVMData(self, uuid, param, row):
        report = self.vm_reports[uuid]
        col = report[param]
        return self.__lookup_data(col, row)

    def GetHostUUID(self):
        report = self.host_report
        if not report:
            return None
        return report.uuid

    def GetHostParamList(self):
        report = self.host_report
        if not report:
            return []
        return report.keys()

    def GetHostData(self, param, row):
        report = self.host_report
        col = report[param]
        return self.__lookup_data(col, row)

    def GetRowTime(self, row):
        return self.__lookup_timestamp(row)

    # extract float from value (<v>) node by col,row
    def __lookup_data(self, col, row):
        # Note: the <rows> nodes are in reverse chronological order, and comprise
        # a timestamp <t> node, followed by self.columns data <v> nodes
        node = self.data_node.childNodes[self.rows - 1 - row].childNodes[col+1]
        return float(node.firstChild.toxml()) # node.firstChild should have nodeType TEXT_NODE

    # extract int from value (<t>) node by row
    def __lookup_timestamp(self, row):
        # Note: the <rows> nodes are in reverse chronological order, and comprise
        # a timestamp <t> node, followed by self.columns data <v> nodes
        node = self.data_node.childNodes[self.rows - 1 - row].childNodes[0]
        return int(node.firstChild.toxml()) # node.firstChild should have nodeType TEXT_NODE

    def Refresh(self, session, override_rrdParams = {}, server = 'http://localhost'):
        rrdParams = dict(self.rrdParams)
        rrdParams.update(override_rrdParams)
        rrdParams['host'] = "true"
        rrdParams['session_id'] = session
        rrdParamstr = "&".join(["%s=%s"  % (k,rrdParams[k]) for k in rrdParams])
        url = "%s/rrd_updates?%s" % (server, rrdParamstr)

        #print "Query: %s" % url

        # this is better than urllib.urlopen() as it raises an Exception on http 401 'Unauthorised' error
        # rather than drop into interactive mode
        sock = urllib.URLopener().open(url)
        xmlsource = sock.read()
        sock.close()

        #myFile = open('debug.xml','w')
        #myFile.write(xmlsource)
        #myFile.close()

        xmldoc = minidom.parseString(xmlsource)
        self.__parse_xmldoc(xmldoc)
        # Update the time used on the next run
        self.rrdParams['start'] = self.end_time + 1 # avoid retrieving same data twice

    def __parse_xmldoc(self, xmldoc):

        # The 1st node contains meta data (description of the data)
        # The 2nd node contains the data
        self.meta_node = xmldoc.firstChild.childNodes[0]
        self.data_node = xmldoc.firstChild.childNodes[1]

        def LookupMetadataBytag(name):
            return int (self.meta_node.getElementsByTagName(name)[0].firstChild.toxml())

        # rows = number of samples per variable
        # columns = number of variables
        self.rows = LookupMetadataBytag('rows')
        self.columns = LookupMetadataBytag('columns')

        # These indicate the period covered by the data
        self.start_time = LookupMetadataBytag('start')
        self.step_time  = LookupMetadataBytag('step')
        self.end_time   = LookupMetadataBytag('end')

        # the <legend> Node describes the variables
        self.legend = self.meta_node.getElementsByTagName('legend')[0]

        # vm_reports matches uuid to per VM report
        self.vm_reports = {}

        # There is just one host_report and its uuid should not change!
        self.host_report = None

        # Handle each column.  (I.e. each variable)
        for col in range(self.columns):
            self.__handle_col(col)

    def __handle_col(self, col):
        # work out how to interpret col from the legend
        col_meta_data = self.legend.childNodes[col].firstChild.toxml()

        # vmOrHost will be 'vm' or 'host'.  Note that the Control domain counts as a VM!
        (cf, vmOrHost, uuid, param) = col_meta_data.split(':')

        if vmOrHost == 'vm':
            # Create a report for this VM if it doesn't exist
            if not self.vm_reports.has_key(uuid):
                self.vm_reports[uuid] = VMReport(uuid)

            # Update the VMReport with the col data and meta data
            vm_report = self.vm_reports[uuid]
            vm_report[param] = col

        elif vmOrHost == 'host':
            # Create a report for the host if it doesn't exist
            if not self.host_report:
                self.host_report = HostReport(uuid)
            elif self.host_report.uuid != uuid:
                raise PerfMonException, "Host UUID changed: (was %s, is %s)" % (self.host_report.uuid, uuid)

            # Update the HostReport with the col data and meta data
            self.host_report[param] = col

        else:
            raise PerfMonException, "Invalid string in <legend>: %s" % col_meta_data


class XenServerCollectd:
    def __init__(self):
        self.hosts = {}
        self.verbose = False # Set to true to make your logs really fat
        self.graphHost = True
        self.xApiIterCpt = 0
        self.xApiDefaultIterCpt = 60 # Reconnect the API every X polls
        self.rrdParams = {}
        self.rrdParams['cf'] = "AVERAGE"
        self.rrdParams['start'] = int(time.time()) - 10
        self.rrdParams['interval'] = 5

    def ConnectToMaster(self, hostname):
        url    = self.hosts[hostname]['url']
        user   = self.hosts[hostname]['user']
        passwd = self.hosts[hostname]['passwd']
        self.hosts[hostname]['session'] = XenAPI.Session(url)
        try:
            self.hosts[hostname]['session'].xenapi.login_with_password(user, passwd)
        except XenAPI.Failure, e:
            if e.details[0] == 'HOST_IS_SLAVE':
                master = e.details[1]
                self.hosts[hostname]['master'] = master
                self._LogVerbose('Found master: %s' % master)
                # Fix to make sure we have a working session to the master
                self.ConnectToMaster(master)
            else:
                self._LogVerbose('Unknown exception while trying to get master server: %s:%s' % (e.details[0], e.details[1]))
        else:
            self._LogVerbose('No exception means we are master: %s' % hostname)
            self.hosts[hostname]['master'] = True


    def Connect(self, hostname=None):
        ''' This is called at the startup of Collectd '''
        # Called at startup

        if hostname:
            self._LogVerbose('Connecting to: %s' % hostname)
            self.ConnectToMaster(hostname)
            self.hosts[hostname]['rrdupdates'] = GetRRDUpdates()

        else:
            self._LogVerbose('Connecting to all hosts')
            for hostname in self.hosts.keys():
                url    = self.hosts[hostname]['url']
                user   = self.hosts[hostname]['user']
                passwd = self.hosts[hostname]['passwd']
                self.ConnectToMaster(hostname)
                self.hosts[hostname]['rrdupdates'] = GetRRDUpdates()


    def Config(self, conf):
        ''' Set the config dictionary hosts[hostname] = {'url': ..,'user': .., 'passwd': ..}  from collectd.conf'''
        if len(conf.children) == 0:
            collectd.error('Module configuration missing')
        #
        for node in conf.children:
            hostname = ''
            user = ''
            passwd = ''
            cluster = None
            if node.key == 'Host':
                hostname = node.values[0]
            for hostchild in node.children:
                if hostchild.key == "User":
                    user = hostchild.values[0]
                elif hostchild.key == 'Password':
                    passwd = hostchild.values[0]
                elif hostchild.key == 'Cluster':
                    cluster = hostchild.values[0]
            self.hosts[hostname] = {'url': "http://%s" % hostname,'user': user, 'passwd': passwd, 'cluster': cluster, 'master': None, 'host': hostname}
            self._LogVerbose('Reading new host from config: %s => %s' % (hostname, self.hosts[hostname]))

    def GetHandle(self, hostname):
        master = self.hosts[hostname]['master']
        if master is True:
            if not self.hosts[hostname]['session'].handle:
                self._LogVerbose('We dont have a session handle for host %s, lets reconnect and get one' % hostname)
                self.Connect(hostname)
                return self.GetHandle(hostname)
            else:
                handle =  self.hosts[hostname]['session'].handle
                self._LogVerbose('We have a session handle for host: %s handle: %s' % (hostname, handle))
                return handle
        elif master is not None and master is not True:
            # we have registered a master server
            self._LogVerbose('We are a slave and this is our master: %s' % master)
            if not self.hosts[master]['session'].handle:
                self._LogVerbose('Our master(%s) does not have a handle, lets get one' % master)
                self.Connect(master)
                return self.GetHandle(hostname)
            else:
                handle = self.hosts[master]['session'].handle
                self._LogVerbose('Our master(%s) has a handle: %s' % (master, handle))
                return handle
        else:
            self._LogVerbose('We do not have a master yet for host: %s. Connecting and trying to get handle' % hostname)
            self.ConnectToMaster(hostname)
            return self.GetHandle(hostname)





    def Read(self):
        ''' This is called by Collectd every $Interval seconds '''
        for hostname in self.hosts.keys():
            # If the connection is gone, reconnect
            if self.hosts[hostname]['master'] is not None:
                self.ConnectToMaster(hostname)

            # Dirt fix: Reconnect every x reads to prevent unhandled api session timeout.
            self.xApiIterCpt += 1
            if self.xApiIterCpt > self.xApiDefaultIterCpt:
                self.Shutdown()
                self.Connect()
                self.xApiIterCpt = 0

            self._LogVerbose('Read(): %s' % self.hosts[hostname]['url'] )
            # Fetch the new http://hostname/rrd_update?.. and parse the new data
            handle = self.GetHandle(hostname)

            self._LogVerbose('Connecting to %s with handle: %s and params: %s' % (self.hosts[hostname]['url'], handle, self.rrdParams))
            try:
                self.hosts[hostname]['rrdupdates'].Refresh(handle, self.rrdParams, self.hosts[hostname]['url'])
            except IOError, e:
                self._LogVerbose('Error fetching rrd updates: %s' % e.message)
            # If the option is set, process the host mectrics data
            cluster = self.hosts[hostname]['cluster']
            if self.graphHost:
                isHost = True
                uuid = self.hosts[hostname]['rrdupdates'].GetHostUUID()
                mectricsData = self._GetRows(hostname, uuid, isHost)
                self._ToCollectd(hostname, uuid, mectricsData, isHost, cluster)

            # Process all row w've found so far for each vm
            for uuid in self.hosts[hostname]['rrdupdates'].GetVMList():
                isHost = False
                mectricsData = self._GetRows(hostname, uuid, isHost)
                self._ToCollectd(hostname, uuid, mectricsData, isHost, cluster)

    def Shutdown(self):
        ''' Disconnect all the active sessions - This is called by Collectd on SIGTERM '''
        for hostname in self.hosts.keys():
            if self.hosts[hostname]['master'] is True:
                self._LogVerbose('Disconnecting master: %s' % hostname)
                self.hosts[hostname]['session'].logout()
            else:
                self._LogVerbose('Not disconnecting as we are not master: %s' % hostname)

    def _ToCollectd(self, hostname, uuid, metricsData, isHost, cluster=None):
        ''' This is where the metrics are sent to Collectd '''
        vmid = ''
        if cluster:
            vmid += '%s.' % cluster
        if isHost:
            vmid += 'host.%s' % uuid
        else:
            vmid += 'vm.%s' % uuid

        for key, value in metricsData.iteritems():
            cltd = collectd.Values(type = 'gauge');
            # naming: host "/" plugin ["-" plugin instance] "/" type ["-" type instance]
            cltd.host = 'xenservers' # xenservers/
            cltd.plugin = vmid # vm-29887edd-6f21-d936-53e5-b4cb2bac3ba0/
            cltd.type_instance = key # cpu0
            cltd.values = [ value ]
            cltd.dispatch()
            self._LogVerbose('Dispatch() data from %s: %s/%s/%s/%s' % (hostname, cltd.host, cltd.plugin, cltd.type_instance, value))

    def _GetRows(self, hostname, uuid, isHost):
        result = {}
        if isHost:
            paramList = self.hosts[hostname]['rrdupdates'].GetHostParamList()
        else:
            paramList = self.hosts[hostname]['rrdupdates'].GetVMParamList(uuid)
        for param in paramList:
            if param != '':
                max_time=0
                data=''
                for row in range(self.hosts[hostname]['rrdupdates'].GetRows()):
                    epoch = self.hosts[hostname]['rrdupdates'].GetRowTime(row)
                    if isHost:
                        dv = str(self.hosts[hostname]['rrdupdates'].GetHostData(param,row))
                    else:
                        dv = str(self.hosts[hostname]['rrdupdates'].GetVMData(uuid,param,row))
                    if epoch > max_time:
                        max_time = epoch
                        data = dv
                result[param] = data
        return result

    def _LogVerbose(self, msg):
        ''' Be verbose, if self.verbose is True'''
        if not self.verbose:
            return
        collectd.info('xenserver-collectd [verbose]: %s' % msg)


# Hooks
xenserverCollectd = XenServerCollectd()
collectd.register_config(xenserverCollectd.Config)
collectd.register_init(xenserverCollectd.Connect)
collectd.register_read(xenserverCollectd.Read)
collectd.register_shutdown(xenserverCollectd.Shutdown)
