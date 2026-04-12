from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.topo import Topo
from mininet.cli import CLI
from mininet.log import setLogLevel

class MonitorTopo(Topo):
    def build(self):
        s1 = self.addSwitch('s1')
        for i in range(1, 5):
            h = self.addHost(f'h{i}', ip=f'10.0.0.{i}/24')
            self.addLink(h, s1)

if __name__ == '__main__':
    setLogLevel('info')
    topo = MonitorTopo()
    net  = Mininet(
        topo       = topo,
        switch     = OVSSwitch,
        controller = RemoteController('c0', ip='127.0.0.1', port=6633)
    )
    net.start()
    print("\n Topology: 4 hosts (h1-h4) connected to switch s1")
    print(" Run 'pingall' then watch Terminal 1 for traffic stats\n")
    CLI(net)
    net.stop()