-- Execute the labeled blocks on the stated node during the failover demonstration.

-- NODE 1: run while all three nodes are healthy.
CREATE DATABASE IF NOT EXISTS gigavolt_ha;
USE gigavolt_ha;
CREATE TABLE IF NOT EXISTS write_probe (
    probe_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    inserted_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    writer_node VARCHAR(64) NOT NULL,
    test_phase VARCHAR(30) NOT NULL,
    PRIMARY KEY (probe_id)
) ENGINE=InnoDB;
INSERT INTO write_probe (writer_node, test_phase)
VALUES (@@hostname, 'before_failover');

-- NODE 2: stop MariaDB only on node 1, then run this statement on node 2.
INSERT INTO gigavolt_ha.write_probe (writer_node, test_phase)
VALUES (@@hostname, 'node1_down');

-- NODE 3 before node-1 recovery, then NODE 1 after it rejoins.
SELECT * FROM gigavolt_ha.write_probe ORDER BY probe_id;
SHOW STATUS WHERE Variable_name IN
  ('wsrep_cluster_size', 'wsrep_cluster_status', 'wsrep_ready', 'wsrep_local_state_comment');
