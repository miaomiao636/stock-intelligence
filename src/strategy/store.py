# -*- coding: utf-8 -*-
"""策略存储模块"""

import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional


class StrategyStore:
    """策略存储"""
    
    def __init__(self):
        self.data_dir = Path(__file__).parent.parent.parent / "data" / "strategy"
    
    def load_advisories(self, date_str: str = None) -> List[Dict]:
        """加载策略建议"""
        if date_str is None:
            date_str = date.today().isoformat()
        
        advisory_file = self.data_dir / "advisories" / f"{date_str}.json"
        if not advisory_file.exists():
            return []
        
        with open(advisory_file) as f:
            return json.load(f)
    
    def update_advisory_status(
        self,
        advisory_id: int,
        status: str,
        operator: str = "user"
    ) -> bool:
        """更新建议状态"""
        
        # 查找所有建议文件
        advisory_dir = self.data_dir / "advisories"
        if not advisory_dir.exists():
            return False
        
        for f in sorted(advisory_dir.glob("*.json"), reverse=True):
            with open(f) as fp:
                advisories = json.load(fp)
            
            for advisory in advisories:
                if advisory.get("advisory_id") == advisory_id:
                    advisory["status"] = status
                    advisory["operator"] = operator
                    
                    if status == "accepted":
                        from datetime import datetime
                        advisory["accepted_at"] = datetime.now().isoformat()
                    elif status == "rejected":
                        from datetime import datetime
                        advisory["rejected_at"] = datetime.now().isoformat()
                    
                    # 保存回文件
                    with open(f, "w") as fp:
                        json.dump(advisories, fp, ensure_ascii=False, indent=2)
                    
                    return True
        
        return False
    
    def get_pending_advisories(self) -> List[Dict]:
        """获取所有待处理的建议"""
        
        advisory_dir = self.data_dir / "advisories"
        if not advisory_dir.exists():
            return []
        
        pending = []
        for f in sorted(advisory_dir.glob("*.json"), reverse=True):
            with open(f) as fp:
                advisories = json.load(fp)
            
            for advisory in advisories:
                if advisory.get("status") == "pending":
                    pending.append(advisory)
        
        return pending
