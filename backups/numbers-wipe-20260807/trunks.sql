--
-- PostgreSQL database dump
--

\restrict 4AP3QtgUeoLczcNADuhxRt6HNEyaw8gx27sCxFtODUqzYbeQUXtw4hLGg7e2D6V

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: trunks; Type: TABLE DATA; Schema: public; Owner: lescall
--

COPY public.trunks (id, name, provider, owner, sip_domain, proxy, username, password, register, caller_id, provider_ips, transport, config_status, last_applied_at, created_at) FROM stdin;
4	alwynantonyben-dhronai-com_plivo-231897	plivo	alwynantonyben@dhronai.com	86116159722292477.zt.plivo.com				0	+91 22 6423 1897		udp	applied	2026-07-12 04:21:02	2026-07-12 04:21:01
5	angelo-dhronai-com_plivo-953160	plivo	angelo@dhronai.com	25935838218519915.zt.plivo.com				0	+91806595 3160		udp	applied	2026-07-13 04:28:26	2026-07-13 04:28:26
6	support-milledger-com_plivo-231897	plivo	support@milledger.com	16584853209835561.zt.plivo.com				0	+912264231897		udp	applied	2026-08-01 08:34:24.805736	2026-08-01 08:10:49.806852
7	info-craftidea-in_plivo-339140	plivo	info@craftidea.in	86116159722292477.zt.plivo.com				0	+91 80 3133 9140		udp	applied	2026-08-05 10:46:33.796997	2026-08-05 10:46:33.423795
\.


--
-- Name: trunks_id_seq; Type: SEQUENCE SET; Schema: public; Owner: lescall
--

SELECT pg_catalog.setval('public.trunks_id_seq', 7, true);


--
-- PostgreSQL database dump complete
--

\unrestrict 4AP3QtgUeoLczcNADuhxRt6HNEyaw8gx27sCxFtODUqzYbeQUXtw4hLGg7e2D6V

