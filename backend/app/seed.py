"""初始化数据：小企业会计准则科目表、报表模板、管理员账号。

科目表依据《小企业会计准则》附录（财会〔2011〕17 号），一级科目 66 个
全部覆盖；明细科目按制造业实务需要补充。

报表模板为「公式驱动」，不是硬编码行。每行 formula 为若干取数项：
  {"codes": ["1001","1002"], "mode": "end", "sign": 1}
mode 取值：
  end     期末余额（按科目余额方向取净额）
  begin   年初余额（上年 12 月末）
  period  本期发生额
  ytd     本年累计发生额
  pl      兼容旧模板的损益类净额；现行资产负债表不再用它自动配平
codes 用编码前缀匹配，写 "1002" 即包含 1002 及其所有下级。
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import hash_password
from .models import Account, Company, Period, ReportTemplate, User

# ---------------------------------------------------------------- 科目表
# (编码, 名称, 类别, 余额方向, 辅助核算, 是否现金类)
CHART = [
    # ===== 资产类 =====
    ("1001", "库存现金", "asset", "dr", "", True),
    ("1002", "银行存款", "asset", "dr", "", True),
    ("1002.01", "银行存款-基本户", "asset", "dr", "", True),
    ("1002.02", "银行存款-一般户", "asset", "dr", "", True),
    ("1012", "其他货币资金", "asset", "dr", "", True),
    ("1101", "短期投资", "asset", "dr", "", False),
    ("1121", "应收票据", "asset", "dr", "customer", False),
    ("1122", "应收账款", "asset", "dr", "customer", False),
    ("1123", "预付账款", "asset", "dr", "supplier", False),
    ("1131", "应收股利", "asset", "dr", "", False),
    ("1132", "应收利息", "asset", "dr", "", False),
    ("1221", "其他应收款", "asset", "dr", "employee", False),
    ("1221.01", "其他应收款-备用金", "asset", "dr", "employee", False),
    ("1231", "其他流动资产", "asset", "dr", "", False),
    ("1401", "材料采购", "asset", "dr", "", False),
    ("1402", "在途物资", "asset", "dr", "", False),
    ("1403", "原材料", "asset", "dr", "", False),
    ("1403.01", "原材料-电子料", "asset", "dr", "", False),
    ("1403.02", "原材料-结构件", "asset", "dr", "", False),
    ("1403.03", "原材料-包装物", "asset", "dr", "", False),
    ("1404", "材料成本差异", "asset", "dr", "", False),
    ("1405", "库存商品", "asset", "dr", "", False),
    ("1405.01", "库存商品-产成品", "asset", "dr", "", False),
    ("1405.02", "库存商品-半成品", "asset", "dr", "", False),
    ("1407", "商品进销差价", "asset", "cr", "", False),
    ("1408", "委托加工物资", "asset", "dr", "", False),
    ("1411", "周转材料", "asset", "dr", "", False),
    ("1411.01", "周转材料-低值易耗品", "asset", "dr", "", False),
    ("1411.02", "周转材料-包装物", "asset", "dr", "", False),
    ("1421", "消耗性生物资产", "asset", "dr", "", False),
    ("1501", "长期债券投资", "asset", "dr", "", False),
    ("1511", "长期股权投资", "asset", "dr", "", False),
    ("1601", "固定资产", "asset", "dr", "", False),
    ("1601.01", "固定资产-房屋建筑物", "asset", "dr", "", False),
    ("1601.02", "固定资产-机器设备", "asset", "dr", "", False),
    ("1601.03", "固定资产-运输设备", "asset", "dr", "", False),
    ("1601.04", "固定资产-电子设备", "asset", "dr", "", False),
    ("1601.05", "固定资产-办公设备", "asset", "dr", "", False),
    ("1601.06", "固定资产-其他", "asset", "dr", "", False),
    ("1602", "累计折旧", "asset", "cr", "", False),
    ("1602.01", "累计折旧-房屋建筑物", "asset", "cr", "", False),
    ("1602.02", "累计折旧-机器设备", "asset", "cr", "", False),
    ("1602.03", "累计折旧-运输设备", "asset", "cr", "", False),
    ("1602.04", "累计折旧-电子设备", "asset", "cr", "", False),
    ("1602.05", "累计折旧-办公设备", "asset", "cr", "", False),
    ("1602.06", "累计折旧-其他", "asset", "cr", "", False),
    ("1604", "在建工程", "asset", "dr", "", False),
    ("1605", "工程物资", "asset", "dr", "", False),
    ("1606", "固定资产清理", "asset", "dr", "", False),
    ("1621", "生产性生物资产", "asset", "dr", "", False),
    ("1622", "生产性生物资产累计折旧", "asset", "cr", "", False),
    ("1701", "无形资产", "asset", "dr", "", False),
    ("1702", "累计摊销", "asset", "cr", "", False),
    ("1801", "长期待摊费用", "asset", "dr", "", False),
    ("1901", "待处理财产损溢", "asset", "dr", "", False),
    # ===== 负债类 =====
    ("2001", "短期借款", "liability", "cr", "", False),
    ("2201", "应付票据", "liability", "cr", "supplier", False),
    ("2202", "应付账款", "liability", "cr", "supplier", False),
    ("2203", "预收账款", "liability", "cr", "customer", False),
    ("2211", "应付职工薪酬", "liability", "cr", "", False),
    ("2211.01", "应付职工薪酬-工资", "liability", "cr", "", False),
    ("2211.02", "应付职工薪酬-职工福利费", "liability", "cr", "", False),
    ("2211.03", "应付职工薪酬-社会保险费", "liability", "cr", "", False),
    ("2211.04", "应付职工薪酬-住房公积金", "liability", "cr", "", False),
    ("2211.05", "应付职工薪酬-工会经费", "liability", "cr", "", False),
    ("2211.06", "应付职工薪酬-职工教育经费", "liability", "cr", "", False),
    ("2221", "应交税费", "liability", "cr", "", False),
    ("2221.01", "应交税费-应交增值税", "liability", "cr", "", False),
    ("2221.01.01", "　进项税额", "liability", "dr", "", False),
    ("2221.01.02", "　销项税额", "liability", "cr", "", False),
    ("2221.01.03", "　进项税额转出", "liability", "cr", "", False),
    ("2221.01.04", "　已交税金", "liability", "dr", "", False),
    ("2221.01.05", "　转出未交增值税", "liability", "dr", "", False),
    ("2221.01.06", "　转出多交增值税", "liability", "cr", "", False),
    ("2221.01.07", "　减免税款", "liability", "dr", "", False),
    ("2221.01.08", "　出口退税", "liability", "cr", "", False),
    ("2221.01.09", "　销项税额抵减", "liability", "dr", "", False),
    ("2221.02", "应交税费-未交增值税", "liability", "cr", "", False),
    ("2221.03", "应交税费-待认证进项税额", "liability", "dr", "", False),
    ("2221.04", "应交税费-待抵扣进项税额", "liability", "dr", "", False),
    ("2221.05", "应交税费-待转销项税额", "liability", "cr", "", False),
    ("2221.06", "应交税费-应交企业所得税", "liability", "cr", "", False),
    ("2221.07", "应交税费-应交个人所得税", "liability", "cr", "", False),
    ("2221.08", "应交税费-应交城市维护建设税", "liability", "cr", "", False),
    ("2221.09", "应交税费-应交教育费附加", "liability", "cr", "", False),
    ("2221.10", "应交税费-应交地方教育附加", "liability", "cr", "", False),
    ("2221.11", "应交税费-应交印花税", "liability", "cr", "", False),
    ("2221.12", "应交税费-应交房产税", "liability", "cr", "", False),
    ("2221.13", "应交税费-应交城镇土地使用税", "liability", "cr", "", False),
    ("2221.14", "应交税费-应交车船税", "liability", "cr", "", False),
    ("2221.15", "应交税费-应交消费税", "liability", "cr", "", False),
    ("2231", "应付利息", "liability", "cr", "", False),
    ("2232", "应付利润", "liability", "cr", "", False),
    ("2241", "其他应付款", "liability", "cr", "", False),
    ("2401", "递延收益", "liability", "cr", "", False),
    ("2501", "长期借款", "liability", "cr", "", False),
    ("2701", "长期应付款", "liability", "cr", "", False),
    # ===== 所有者权益 =====
    ("3001", "实收资本", "equity", "cr", "", False),
    ("3002", "资本公积", "equity", "cr", "", False),
    ("3101", "盈余公积", "equity", "cr", "", False),
    ("3103", "本年利润", "equity", "cr", "", False),
    ("3104", "利润分配", "equity", "cr", "", False),
    # ===== 成本类 =====
    ("4001", "生产成本", "cost", "dr", "", False),
    ("4001.01", "生产成本-基本生产成本", "cost", "dr", "", False),
    ("4001.02", "生产成本-辅助生产成本", "cost", "dr", "", False),
    ("4101", "制造费用", "cost", "dr", "", False),
    ("4101.01", "制造费用-折旧费", "cost", "dr", "", False),
    ("4101.02", "制造费用-水电费", "cost", "dr", "", False),
    ("4101.03", "制造费用-间接人工", "cost", "dr", "", False),
    ("4101.04", "制造费用-修理费", "cost", "dr", "", False),
    ("4101.05", "制造费用-低值易耗品摊销", "cost", "dr", "", False),
    ("4101.06", "制造费用-其他", "cost", "dr", "", False),
    ("4301", "研发支出", "cost", "dr", "", False),
    ("4401", "工程施工", "cost", "dr", "", False),
    ("4403", "机械作业", "cost", "dr", "", False),
    # ===== 损益类 =====
    ("5001", "主营业务收入", "pl", "cr", "", False),
    ("5001.01", "主营业务收入-产品销售", "pl", "cr", "", False),
    ("5051", "其他业务收入", "pl", "cr", "", False),
    ("5111", "投资收益", "pl", "cr", "", False),
    ("5301", "营业外收入", "pl", "cr", "", False),
    ("5401", "主营业务成本", "pl", "dr", "", False),
    ("5402", "其他业务成本", "pl", "dr", "", False),
    ("5403", "税金及附加", "pl", "dr", "", False),
    ("5403.01", "税金及附加-城市维护建设税", "pl", "dr", "", False),
    ("5403.02", "税金及附加-教育费附加", "pl", "dr", "", False),
    ("5403.03", "税金及附加-地方教育附加", "pl", "dr", "", False),
    ("5403.04", "税金及附加-印花税", "pl", "dr", "", False),
    ("5403.05", "税金及附加-房产税", "pl", "dr", "", False),
    ("5403.06", "税金及附加-土地使用税", "pl", "dr", "", False),
    ("5403.07", "税金及附加-车船税", "pl", "dr", "", False),
    ("5601", "销售费用", "pl", "dr", "", False),
    ("5601.01", "销售费用-职工薪酬", "pl", "dr", "", False),
    ("5601.02", "销售费用-运输费", "pl", "dr", "", False),
    ("5601.03", "销售费用-广告宣传费", "pl", "dr", "", False),
    ("5601.04", "销售费用-业务招待费", "pl", "dr", "", False),
    ("5601.05", "销售费用-折旧费", "pl", "dr", "", False),
    ("5601.06", "销售费用-售后服务费", "pl", "dr", "", False),
    ("5601.07", "销售费用-其他", "pl", "dr", "", False),
    ("5602", "管理费用", "pl", "dr", "", False),
    ("5602.01", "管理费用-职工薪酬", "pl", "dr", "", False),
    ("5602.02", "管理费用-折旧费", "pl", "dr", "", False),
    ("5602.03", "管理费用-办公费", "pl", "dr", "", False),
    ("5602.04", "管理费用-差旅费", "pl", "dr", "", False),
    ("5602.05", "管理费用-业务招待费", "pl", "dr", "", False),
    ("5602.06", "管理费用-研究费用", "pl", "dr", "", False),
    ("5602.07", "管理费用-中介服务费", "pl", "dr", "", False),
    ("5602.08", "管理费用-水电费", "pl", "dr", "", False),
    ("5602.09", "管理费用-其他", "pl", "dr", "", False),
    ("5603", "财务费用", "pl", "dr", "", False),
    ("5603.01", "财务费用-利息支出", "pl", "dr", "", False),
    ("5603.02", "财务费用-手续费", "pl", "dr", "", False),
    ("5603.03", "财务费用-汇兑损益", "pl", "dr", "", False),
    ("5711", "营业外支出", "pl", "dr", "", False),
    ("5711.01", "营业外支出-坏账损失", "pl", "dr", "", False),
    ("5711.02", "营业外支出-税收滞纳金", "pl", "dr", "", False),
    ("5711.03", "营业外支出-非常损失", "pl", "dr", "", False),
    ("5801", "所得税费用", "pl", "dr", "", False),
]


def T(codes, mode="end", sign=1):
    return {"codes": codes, "mode": mode, "sign": sign}


def R(key, label, indent=0, terms=None, refs=None, bold=False, top=False, no=None, total=None):
    return {
        "key": key,
        "label": label,
        "no": no,  # 报表行次，照《小企业会计准则》附录表格编排
        "indent": indent,
        "terms": terms or [],
        "refs": refs or [],
        "bold": bold,
        "top": top,  # 分节小标题，无金额
        "total": total,  # "assets" / "liab_equity"，供程序定位总计行
    }


TEMPLATE_REV = 5  # 模板版本，改结构时 +1，启动时自动覆盖库里的旧定义


BALANCE_SHEET = {
    "title": "资产负债表",
    "subtitle": "会小企 01 表",
    "rev": TEMPLATE_REV,
    "columns": [{"key": "end", "label": "期末余额"}, {"key": "begin", "label": "年初余额"}],
    "left": [
        R("A0", "流动资产：", top=True),
        R("A1", "货币资金", 1, [T(["1001", "1002", "1012"])], no=1),
        R("A2", "短期投资", 1, [T(["1101"])], no=2),
        R("A3", "应收票据", 1, [T(["1121"])], no=3),
        R("A4", "应收账款", 1, [T(["1122"])], no=4),
        R("A5", "预付账款", 1, [T(["1123"])], no=5),
        R("A6", "应收股利", 1, [T(["1131"])], no=6),
        R("A7", "应收利息", 1, [T(["1132"])], no=7),
        R("A8", "其他应收款", 1, [T(["1221"])], no=8),
        R("A9", "存货", 1, [
            T(["1401", "1402", "1403", "1404", "1405", "1408", "1411", "1421", "4001"]),
            T(["1407"], sign=-1),
        ], no=9),
        R("A10", "其中：原材料", 2, [T(["1401", "1402", "1403"])], no=10),
        R("A11", "在产品", 2, [T(["4001"])], no=11),
        R("A12", "库存商品", 2, [T(["1405"])], no=12),
        R("A13", "周转材料", 2, [T(["1411"])], no=13),
        R("A14", "其他流动资产", 1, [T(["1231"])], no=14),
        R("A15", "流动资产合计", 0, refs=[
            ["A1"], ["A2"], ["A3"], ["A4"], ["A5"], ["A6"], ["A7"], ["A8"], ["A9"], ["A14"],
        ], bold=True, no=15),
        R("A16", "非流动资产：", top=True),
        R("A17", "长期债券投资", 1, [T(["1501"])], no=16),
        R("A18", "长期股权投资", 1, [T(["1511"])], no=17),
        R("A19", "固定资产原价", 1, [T(["1601"])], no=18),
        R("A20", "减：累计折旧", 1, [T(["1602"])], no=19),
        R("A21", "固定资产账面价值", 1, refs=[["A19"], ["A20", -1]], no=20),
        R("A22", "在建工程", 1, [T(["1604"])], no=21),
        R("A23", "工程物资", 1, [T(["1605"])], no=22),
        R("A24", "固定资产清理", 1, [T(["1606"])], no=23),
        R("A25", "生产性生物资产", 1, [T(["1621"]), T(["1622"], sign=-1)], no=24),
        R("A26", "无形资产", 1, [T(["1701"]), T(["1702"], sign=-1)], no=25),
        R("A27", "开发支出", 1, [], no=26),
        R("A28", "长期待摊费用", 1, [T(["1801"])], no=27),
        R("A29", "其他非流动资产", 1, [T(["1901"])], no=28),
        R("A30", "非流动资产合计", 0, refs=[
            ["A17"], ["A18"], ["A21"], ["A22"], ["A23"], ["A24"], ["A25"], ["A26"],
            ["A27"], ["A28"], ["A29"],
        ], bold=True, no=29),
        R("A31", "资产总计", 0, refs=[["A15"], ["A30"]], bold=True, no=30, total="assets"),
    ],
    "right": [
        R("B0", "流动负债：", top=True),
        R("B1", "短期借款", 1, [T(["2001"])], no=31),
        R("B2", "应付票据", 1, [T(["2201"])], no=32),
        R("B3", "应付账款", 1, [T(["2202"])], no=33),
        R("B4", "预收账款", 1, [T(["2203"])], no=34),
        R("B5", "应付职工薪酬", 1, [T(["2211"])], no=35),
        R("B6", "应交税费", 1, [T(["2221"])], no=36),
        R("B7", "应付利息", 1, [T(["2231"])], no=37),
        R("B8", "应付利润", 1, [T(["2232"])], no=38),
        R("B9", "其他应付款", 1, [T(["2241"])], no=39),
        R("B10", "其他流动负债", 1, [], no=40),
        R("B11", "流动负债合计", 0, refs=[
            ["B1"], ["B2"], ["B3"], ["B4"], ["B5"], ["B6"], ["B7"], ["B8"], ["B9"], ["B10"],
        ], bold=True, no=41),
        R("B12", "非流动负债：", top=True),
        R("B13", "长期借款", 1, [T(["2501"])], no=42),
        R("B14", "长期应付款", 1, [T(["2701"])], no=43),
        R("B15", "递延收益", 1, [T(["2401"])], no=44),
        R("B16", "其他非流动负债", 1, [], no=45),
        R("B17", "非流动负债合计", 0, refs=[["B13"], ["B14"], ["B15"], ["B16"]], bold=True, no=46),
        R("B18", "负债合计", 0, refs=[["B11"], ["B17"]], bold=True, no=47),
        # 官方表在「负债合计」后留 6 个空行，让「所有者权益」与左栏「生产性生物资产」同一行对齐
        R("B19", "", 0, []), R("B20", "", 0, []), R("B21", "", 0, []),
        R("B22", "", 0, []), R("B23", "", 0, []), R("B24", "", 0, []),
        R("B25", "所有者权益（或股东权益）：", top=True),
        R("B26", "实收资本（或股本）", 1, [T(["3001"])], no=48),
        R("B27", "资本公积", 1, [T(["3002"])], no=49),
        R("B28", "盈余公积", 1, [T(["3101"])], no=50),
        R("B29", "未分配利润", 1, [T(["3103", "3104"])], no=51),
        R("B30", "所有者权益（或股东权益）合计", 0, refs=[["B26"], ["B27"], ["B28"], ["B29"]],
          bold=True, no=52),
        R("B31", "负债和所有者权益（或股东权益）总计", 0, refs=[["B18"], ["B30"]],
          bold=True, no=53, total="liab_equity"),
    ],
}

PROFIT = {
    "title": "利润表",
    "subtitle": "会小企 02 表",
    "rev": TEMPLATE_REV,
    # 与官方表格一致：本年累计在左，本月金额在右
    "columns": [{"key": "ytd", "label": "本年累计金额"}, {"key": "period", "label": "本月金额"}],
    "rows": [
        R("P1", "一、营业收入", 0, [T(["5001", "5051"], "period")], no=1),
        R("P2", "减：营业成本", 0, [T(["5401", "5402"], "period")], no=2),
        R("P3", "税金及附加", 0, [T(["5403"], "period")], no=3),
        R("P4", "其中：消费税", 1, [], no=4),
        R("P5", "营业税", 1, [], no=5),
        R("P6", "城市维护建设税", 1, [T(["5403.01"], "period")], no=6),
        R("P7", "资源税", 1, [], no=7),
        R("P8", "土地增值税", 1, [], no=8),
        R("P9", "城镇土地使用税、房产税、车船税、印花税", 1,
          [T(["5403.04", "5403.05", "5403.06", "5403.07"], "period")], no=9),
        R("P10", "教育费附加、矿产资源补偿税、排污费", 1,
          [T(["5403.02", "5403.03"], "period")], no=10),
        R("P11", "销售费用", 0, [T(["5601"], "period")], no=11),
        R("P12", "其中：商品维修费", 1, [T(["5601.06"], "period")], no=12),
        R("P13", "广告费和业务宣传费", 1, [T(["5601.03"], "period")], no=13),
        R("P14", "管理费用", 0, [T(["5602"], "period")], no=14),
        R("P15", "其中：开办费", 1, [], no=15),
        R("P16", "业务招待费", 1, [T(["5602.05"], "period")], no=16),
        R("P17", "研究费用", 1, [T(["5602.06"], "period")], no=17),
        R("P18", "财务费用", 0, [T(["5603"], "period")], no=18),
        R("P19", "其中：利息费用（收入以“-”号填列）", 1, [T(["5603.01"], "period")], no=19),
        R("P20", "加：投资收益（损失以“-”号填列）", 0, [T(["5111"], "period")], no=20),
        R("P21", "二、营业利润（亏损以“-”号填列）", 0, refs=[
            ["P1"], ["P2", -1], ["P3", -1], ["P11", -1], ["P14", -1], ["P18", -1], ["P20"],
        ], bold=True, no=21),
        R("P22", "加：营业外收入", 0, [T(["5301"], "period")], no=22),
        R("P23", "其中：政府补助", 1, [], no=23),
        R("P24", "减：营业外支出", 0, [T(["5711"], "period")], no=24),
        R("P25", "其中：坏账损失", 1, [T(["5711.01"], "period")], no=25),
        R("P26", "无法收回的长期债券投资损失", 1, [], no=26),
        R("P27", "无法收回的长期股权投资损失", 1, [], no=27),
        R("P28", "自然灾害等不可抗力因素造成的损失", 1, [T(["5711.03"], "period")], no=28),
        R("P29", "税收滞纳金", 1, [T(["5711.02"], "period")], no=29),
        R("P30", "三、利润总额（亏损总额以“-”号填列）", 0,
          refs=[["P21"], ["P22"], ["P24", -1]], bold=True, no=30),
        R("P31", "减：所得税费用", 0, [T(["5801"], "period")], no=31),
        R("P32", "四、净利润（净亏损以“-”号填列）", 0,
          refs=[["P30"], ["P31", -1]], bold=True, no=32),
    ],
}


def ensure_periods(db: Session, year: int):
    have = {p.code for p in db.scalars(select(Period)).all()}
    for m in range(1, 13):
        code = f"{year:04d}-{m:02d}"
        if code not in have:
            db.add(Period(code=code, year=year, month=m, status="open"))


def seed(db: Session, company_name="财务公司", start_period=None):
    """幂等初始化。返回是否新建了科目表。"""
    created = False

    comp = db.scalar(select(Company).limit(1))
    if not comp:
        comp = Company(
            name=company_name,
            start_period=start_period or f"{datetime.now().year}-01",
        )
        db.add(comp)
        db.flush()

    if db.scalar(select(Account).limit(1)) is None:
        by_code = {}
        for code, name, cat, direction, aux, cash in CHART:
            a = Account(
                code=code,
                name=name,
                cat=cat,
                direction=direction,
                level=code.count(".") + 1,
                aux_types=aux,
                is_cash=cash,
            )
            db.add(a)
            by_code[code] = a
        db.flush()
        for code, a in by_code.items():
            if "." in code:
                parent = by_code.get(code.rsplit(".", 1)[0])
                if parent:
                    a.parent_id = parent.id
                    parent.is_leaf = False
        created = True

    # 报表模板带 rev 版本号：结构改了就 +1，启动时自动覆盖库里的旧定义，
    # 不用手工改数据，也不会覆盖用户对「外观」的本地调整（模板本身不含外观）
    have_tpl = {t.code: t for t in db.scalars(select(ReportTemplate)).all()}
    for code, name, definition in (
        ("balance_sheet", "资产负债表", BALANCE_SHEET),
        ("profit", "利润表", PROFIT),
    ):
        t = have_tpl.get(code)
        if t is None:
            db.add(ReportTemplate(code=code, name=name, definition=definition))
        elif (t.definition or {}).get("rev", 0) < definition.get("rev", 0):
            t.definition = definition
            t.updated_at = datetime.now()

    if db.scalar(select(User).limit(1)) is None:
        pw, salt = hash_password("admin123")
        db.add(User(username="admin", password_hash=pw, salt=salt, name="管理员", role="admin"))

    year = int((comp.start_period or f"{datetime.now().year}-01")[:4])
    ensure_periods(db, year)
    ensure_periods(db, year + 1)
    db.commit()
    return created
