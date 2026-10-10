"""知识库（阶段 2）：中文分词 + 自建倒排 + B25 检索。

目录结构（``01`` §4.6）：tokenizer / indexer / retriever。S-31 禁止 MongoDB $text，
S-32 检索签名固定，S-33 ACL 在打分后过滤，S-34 不新增基础设施。
"""
