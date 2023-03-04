class TestVar:
    def test_var_log(self, image_path):
        p = image_path / 'var' / 'log'
        assert p.exists(), '/var/log does not exist'
        assert p.is_dir(), '/var/log is no directory'

        c = set(i.relative_to(p).as_posix() for i in p.glob('*'))
        c.difference_update((
            'apt',
            'btmp',
            'chrony',
            'faillog',
            'journal',  # systemd persistent journal
            'lastlog',
            'runit',
            'unattended-upgrades',
            'README',
            'private',
            'wtmp',
            # From kali-linux-headless
            'apache2',
            'fontconfig.log',
            'inetsim',
            'mysql',
            'nginx',
            'openvpn',
            'postgresql',
            'samba',
            'stunnel4',
            'sysstat',
            # From kali-desktop-large
            'chkrootkit',
            'dradis',
            # From kali-linux-everything
            'defectdojo',
            'eaphammer',
            'exim4',
            'freeradius',
            'freeradius-wpe',
            'fsck',
            'gvm',
            'hostapd-wpe',
            'redis',
            'redsnarf',
            'tor',
            # From kali-desktop-core
            'speech-dispatcher',
            # From kali-desktop-kde
            'sddm.log',
        ))

        assert len(c) == 0, '/var/log contains unexpected files: {}'.format(', '.join(c))
