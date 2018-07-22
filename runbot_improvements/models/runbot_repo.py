# -*- coding: utf-8 -*-
##############################################################################
#
#    Odoo, Open Source Management Solution
#    Copyright (C) 2004-2015 Odoo s.a. (<http://odoo.com>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
import subprocess
import resource
import os

from openerp import models, fields, api
from openerp.addons.runbot import runbot
from openerp.addons.runbot.runbot import log, dashes, mkdirs, grep, rfind, lock, locked, nowait, run, now, dt2time, s2human, flatten, decode_utf, uniq_list, fqdn
from openerp.addons.runbot.runbot import _re_error, _re_warning, _re_job, _logger

class runbot_repo(models.Model):
    _inherit = "runbot.repo"

    nobuild = fields.Boolean("Do not build")
    db_name = fields.Char("Database name to replicate")
    docoverage = fields.Boolean("Do coverage report")

    @api.model
    def update_git(self, repo):
        super(runbot_repo, self).update_git(repo=repo)
        if repo.nobuild:
            self.env['runbot.build'].search([('repo_id', '=', repo.id),
                                             ('state', '=', 'pending'),
                                             ('branch_id.sticky', '=', False)
                ]).write({'state': 'done', 'result': 'skipped'})


class runbot_build(models.Model):
    _inherit = "runbot.build"

    @api.model
    def spawncwd(self, cmd, lock_path, log_path, cpu_limit=None, shell=False, cwd=None):
        def preexec_fn():
            os.setsid()
            if cpu_limit:
                # set soft cpulimit
                soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
                r = resource.getrusage(resource.RUSAGE_SELF)
                cpu_time = r.ru_utime + r.ru_stime
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_time + cpu_limit, hard))
            # close parent files
            os.closerange(3, os.sysconf("SC_OPEN_MAX"))
            lock(lock_path)
        out=open(log_path,"w")
        _logger.info("spawn: %s in %s stdout: %s", ' '.join(cmd), str(cwd), log_path)
        p=subprocess.Popen(cmd, stdout=out, stderr=out, preexec_fn=preexec_fn, shell=shell, cwd=cwd)
        return p.pid
        
    @api.model
    def job_20_test_all(self, build, lock_path, log_path):
        if build.repo_id.docoverage:
            build._log('test_all', 'Start test all modules')
            path = build.path()
            self.pg_createdb("%s-all" % build.dest)
            cmd, mods = build.cmd()
            if grep(build.server("tools/config.py"), "test-enable"):
                cmd.append("--test-enable")
            cmd += ['-d', '%s-all' % build.dest, '-i', mods,
                    '--stop-after-init', '--log-level=test',
                    '--max-cron-threads=0']
            cmd = ['coverage', 'run', '--include=openerp/addons/*'] + cmd[1:]
            # reset job_start to an accurate job_20 job_time
            build.write({'job_start': now()})
            build._log('job_20_coverage', " ".join(cmd))
            return self.spawncwd(cmd, lock_path, log_path, cpu_limit=3500,
                cwd=path)
        else:
            return super(runbot_build, self).job_20_test_all(build, lock_path,
                log_path)

    @api.model
    def job_22_coverage_report(self, build, lock_path, log_path):
        if build.repo_id.docoverage:
            path = build.path()
            cmd, mods = build.cmd()
            if mods:
                include = ",".join(["openerp/addons/%s/*" % mod.replace(" ","")
                        for mod in mods.split(",")
                    ])
            else:
                include = "openerp/addons/*"
            cmd = ['coverage', 'report' ,'--include=%s' % include]
            build._log('coverage_report', " ".join(cmd))
            return self.spawncwd(cmd, lock_path, log_path, cpu_limit=None,
                cwd=path)
        else:
            return 0

    @api.model
    def job_23_coverage_report_html(self, build, lock_path, log_path):
        if build.repo_id.docoverage:
            path = build.path()
            cmd, mods = build.cmd()
            if mods:
                include = ",".join(["openerp/addons/%s/*" % mod for mod in mods])
            else:
                include = "openerp/addons/*"
            cmd = ['coverage', 'html', '--include=%s' % include, "-d",
                "logs/coverage"]
            return self.spawncwd(cmd, lock_path, log_path, cpu_limit=None,
                cwd=path)
        else:
            return 0

    @api.model
    def job_25_restore(self, build, lock_path, log_path):
        if not build.repo_id.db_name:
            return 0
        self.pg_createdb("%s-all" % build.dest)
        cmd = "pg_dump %s | psql %s-all" % (build.repo_id.db_name, build.dest)
        return self.spawn(cmd, lock_path, log_path, cpu_limit=None, shell=True)

    @api.model
    def job_26_upgrade(self, build, lock_path, log_path):
        if not build.repo_id.db_name:
            return 0
        cmd, mods = build.cmd()
        cmd += ['-d', '%s-all' % build.dest, '-u', 'all', '--stop-after-init',
        '--log-level=debug']
        return self.spawn(cmd, lock_path, log_path, cpu_limit=None)

class RunbotControllerXP(runbot.RunbotController):

    def build_info(self, build):
        res = super(RunbotControllerXP, self).build_info(build)
        res['docoverage'] = build.repo_id.docoverage
        return res