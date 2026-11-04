"""Optimized DNS reads retain weighted rankings and the requested date window."""
import time

import pytest

from test_app import client
from test_compact_history import seed_dns


@pytest.mark.parametrize('query', [
    'period=90', 'period=1', 'period=90&status=blocked',
    'period=90&search=example.test', 'period=90&client=10.77.0.10',
    'start=2000-01-01&end=2000-01-02',
])
def test_rankings_and_latest_match_full_history(client, query):
    from adguard_dns_reports import create_app
    from adguard_dns_reports.database import db_connect, create_schema
    from adguard_dns_reports.history import archive_dns
    from adguard_dns_reports.reports import filters
    from adguard_dns_reports.dns_dashboard import dashboard_data
    now = int(time.time())
    c = db_connect()
    create_schema(c)
    for i in range(200):
        seed_dns(c, str(i), now-5*86400+i, blocked=i%2)
        seed_dns(c, 'recent-'+str(i), now-1000+i, domain='recent.test', blocked=i%3 == 0)
    c.execute("UPDATE queries SET ip='10.77.0.20',hostname='SECOND-PC',service='Second' WHERE uid='recent-1'")
    archive_dns(c, now)
    c.commit()
    with create_app().test_request_context('/dns?'+query):
        where, values, _ = filters()
        summary, services, clients, domains, rows, choices = dashboard_data(c, where, values)
        expected = c.execute('SELECT COALESCE(SUM(hits),0),COUNT(DISTINCT ip),'
                             'COALESCE(SUM(blocked*hits),0) FROM dns_report WHERE '+where, values).fetchone()
        assert tuple(summary.values()) == tuple(expected)
        for field, ranking in [('service', services), ('ip', clients), ('domain', domains)]:
            expected = dict(c.execute('SELECT '+field+',SUM(hits) FROM dns_report WHERE '+where+
                                      ' GROUP BY '+field, values))
            assert {r.get('ip', r['name']):r['hits'] for r in ranking} == expected
        expected = c.execute('SELECT uid,ts,record_type FROM dns_report WHERE '+where+
                             ' ORDER BY ts DESC LIMIT 150', values).fetchall()
        assert [(r['uid'],r['ts'],r['record_type']) for r in rows] == [tuple(r) for r in expected]
        # Computer choices must still include devices outside the selected period/filter.
        assert {r['ip'] for r in choices} == {'10.77.0.10','10.77.0.20'}
    c.close()


def test_recent_top_rows_use_timestamp_index_without_sorting_history(client):
    from adguard_dns_reports import create_app
    from adguard_dns_reports.database import db_connect, create_schema
    from adguard_dns_reports.dns_dashboard import SOURCES
    from adguard_dns_reports.reports import filters
    c = db_connect(); create_schema(c)
    with create_app().test_request_context('/dns?period=90'):
        where, values, _ = filters()
        plan = [r[3] for r in c.execute('EXPLAIN QUERY PLAN SELECT * FROM ('+SOURCES[0]+') WHERE '+
                                       where+' ORDER BY ts DESC LIMIT 150', values)]
        assert any('idx_queries_ts' in step for step in plan)
        assert not any('TEMP B-TREE FOR ORDER BY' in step for step in plan)
    c.close()
