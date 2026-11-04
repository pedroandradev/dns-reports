"""Read recent DNS and daily summaries without repeatedly scanning their union."""
from collections import Counter


# Separate sources allow SQLite to use idx_queries_ts for the recent top rows.
# Sorting the combined history first would sort every retained DNS event.
SOURCES = (
    "SELECT *,1 AS hits,ts AS first_ts,ts AS last_ts,'event' AS record_type,"
    "ts AS period_start,ts AS period_end FROM queries",
    "SELECT '' AS uid,last_ts AS ts,*, 'daily' AS record_type FROM dns_daily",
)


def dashboard_data(conn, where, values):
    services, clients, domains = Counter(), Counter(), Counter()
    client_names, names, latest = {}, {}, []
    total = blocked = 0
    for source in SOURCES:
        # Reduce repetitions before passing data to Python, then build all four
        # totals/rankings from this one result instead of scanning four times.
        groups = conn.execute(
            "SELECT ip,MAX(CASE WHEN hostname!=ip THEN hostname ELSE '' END) AS name,"
            "domain,service,SUM(hits) AS hits,SUM(blocked*hits) AS blocked "
            "FROM (" + source + ") WHERE " + where + " GROUP BY ip,domain,service", values)
        for row in groups:
            hits = row['hits']
            total += hits
            blocked += row['blocked']
            services[row['service']] += hits
            clients[row['ip']] += hits
            domains[row['domain']] += hits
            client_names[row['ip']] = max(client_names.get(row['ip'], ''), row['name'])
        latest.extend(conn.execute("SELECT * FROM (" + source + ") WHERE " + where +
                                   " ORDER BY ts DESC LIMIT 150", values))
        # The computer picker includes the whole history, independent of filters.
        for row in conn.execute("SELECT ip,MAX(CASE WHEN hostname!=ip THEN hostname ELSE '' END) AS name "
                                "FROM (" + source + ") GROUP BY ip"):
            names[row['ip']] = max(names.get(row['ip'], ''), row['name'])
    def ranking(counts, limit):
        return [dict(name=name, hits=hits) for name, hits in counts.most_common(limit)]
    client_ranking = [dict(ip=ip, name=client_names[ip] or ip, hits=hits)
                      for ip, hits in clients.most_common(12)]
    choices = [dict(ip=ip, name=name) for ip, name in sorted(names.items(), key=lambda item: (item[1], item[0]))]
    return (dict(count=total, computers=len(clients), blocked=blocked),
            ranking(services, 12), client_ranking, ranking(domains, 10),
            sorted(latest, key=lambda row: row['ts'], reverse=True)[:150], choices)
