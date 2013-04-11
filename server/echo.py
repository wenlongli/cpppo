#! /usr/bin/env python3

# 
# Cpppo -- Communication Protocol Python Parser and Originator
# 
# Copyright (c) 2013, Hard Consulting Corporation.
# 
# Cpppo is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.  See the LICENSE file at the top of the source tree.
# 
# Cpppo is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU General Public License for more details.
# 

from __future__ import absolute_import
from __future__ import print_function

__author__                      = "Perry Kundert"
__email__                       = "perry@hardconsulting.com"
__copyright__                   = "Copyright (c) 2013 Hard Consulting Corporation"
__license__                     = "GNU General Public License, Version 3 (or later)"


"""
echo	-- Implementation of UNIX "echo" server

USAGE
    python -m cpppo.server.echo

"""

import errno
import logging
import os
import select
import socket
import sys
import threading
import time
import traceback
try:
    from reprlib import repr as repr
except ImportError:
    from repr import repr as repr

import cpppo
from   cpppo import misc

address				= ('0.0.0.0', 8007)

if __name__ == "__main__":
    logging.basicConfig( **cpppo.log_cfg )

log				= logging.getLogger( "echo.srv" )

class echo( cpppo.fsm ):
    """Collects a line of data out of our state machine's data
    path+self.context+scontext, into data artifact, at path.echo"""
    def __init__( self, name=None, address=address, **kwds ):
        super( echo, self ).__init__( name=name, **kwds )
        self.address		= address
        
    def process( self, source, machine=None, path=None, data=None ):
        """Once our machine has accepted a sentence of the "echo" grammar and
        terminated, we process it.  It just copies the raw data collected by our
        state machine (we'll use its context), and restarts our sub-machine for
        the next line."""
        ours			= self.context( path )
        subs			= self.initial.context( ours, '_' )
        log.info("recv: data[%s] = data[%s]: %r", ours, subs, data[subs] )
        data[ours]		= data[subs]
        del data[subs]

# Our FSM is described in str symbols; synonymous for bytes on Python2, but
# utf-8 on Python3 so encode them

def echo_machine( name ):
    """Accept a line of input, and then loop. Sub-machine terminates at earliest
    match (non-greedy), causing echo.transition to trigger .process (which resets our
    sub-machine to initial state), and then we move to the next state (loops),
    allowing us to immediately run."""
    machine			= echo( name=name, initial='.*\n', context="echo",
                                        terminal=True,
                                        alphabet=cpppo.type_bytes_iter,
                                        fsm_alphabet=cpppo.type_bytes_iter,
                                        fsm_encoder=cpppo.type_str_encoder,
                                        fsm_typecode=cpppo.type_bytes_array_symbol )
    machine[None]		= machine
    return machine

# Decorates any function( sock, ..., timeout=, [...]), and waits for its sock
# (must be the first positional arg) to report readable w/in timeout before
# executing.  Returns None if not readable.  Supply the desired default timeout,
# if other than 0.
def readable( timeout=0 ):
    def decorator( function ):
        import functools
        @functools.wraps( function )
        def wrapper( *args, **kwds ):
            if 'timeout' in kwds:
                timeout			= kwds['timeout']
                del kwds['timeout']
            try:
                r, w, e		= select.select( [args[0].fileno()], [], [], timeout )
            except select.error as exc:
                log.debug( "select: %r", exc )
                if exc.arg[0] != errno.EINTR:
                    raise
            if r:
                return function( *args, **kwds )
            return None
        return wrapper
    return decorator
        
@readable()
def recv( conn, maxlen=1024 ):
    """Non-blocking recv via. select.  Return None if no data received within
    timeout (default is immediate timeout).  Otherwise, the data payload; zero
    length data implies EOF."""
    try:
        msg			= conn.recv( maxlen ) # b'' (EOF) or b'<data>'
    except socket.error as exc: # No connection; same as EOF
        log.debug( "recv %s: %r", conn, exc )
        msg			= b''
    return msg

@readable(timeout=0)
def accept( conn ):
    return conn.accept()


def drain( conn, timeout=.1 ):
    """Send EOF, drain and close connection cleanly, returning any data
    received.  Will immediately detect an incoming EOF on connection and close,
    otherwise waits timeout for incoming EOF; if exception, assumes that the
    connection is dead (same as EOF)"""
    try:
        conn.shutdown( socket.SHUT_WR )
    except socket.error as exc: # No connection; same as EOF
        log.debug( "shutdown %s: %r", conn, exc )
        msg			= b''
    else:
        msg			= recv( conn, timeout=timeout )

    try:
        conn.close()
    except socket.error as exc: # Already closed
        log.debug( "close %s: %r", conn, exc )
        pass

    return msg


def echo_server( conn, addr ):
    """Serve one echo client 'til EOF; then close the socket"""
    source			= cpppo.chainable()
    data			= cpppo.dotdict()
    echo_line			= echo_machine( "echo_%s" % addr[1] )
    sequence			= echo_line.run( source=source, data=data, greedy=False )
    while True:
        msg			= recv( conn, timeout=None ) # blocking
        if not msg: # None or empty
            log.info( "%s recv: %s", misc.centeraxis( echo_line, 25, clip=True ), repr( msg ) if msg else "EOF" )
            break
        source.chain( msg )
        log.info( "%s recv: %5d: %s", misc.centeraxis( echo_line, 25, clip=True ), len( msg ), repr( msg ))

        # See if a line has been recognized, stopping at terminal state
        for mch, sta in sequence:
            if sta is None:
                break # No more transitions available on source input, but not terminal
        if sta:
            # Terminal state.  Echo, and carry on
            log.info( "%s: data: %r", misc.centeraxis( echo_line, 25, clip=True ), data )
            conn.send( data.echo )
            echo_line.reset()
            sequence		= echo_line.run( source=source, data=data, greedy=False )
        else:
            # Out of input, no complete line of echo input acquired.  Wait for more.
            log.debug( "%s: end of input", misc.centeraxis( echo_line, 25, clip=True ))
 
    log.info( "%s done: %s" % ( misc.centeraxis( echo_line, 25, clip=True ), repr( data )))

class server_thread( threading.Thread ):
    """A generic server handler.  Supply a handler taking an open socket
    connection to target=... Assumes at least one or two arg=(conn,[addr,[...]])"""
    def __init__( self, **kwds ):
        super( server_thread, self ).__init__( **kwds )
        self.conn		= kwds['args'][0]
        self.addr	        = kwds['args'][1] if len( kwds['args'] ) > 1 else None

    def run( self ):
        log.info("%s.echo service PID [%5d/%5d] starting on %r",
                 __package__, os.getpid(), self.ident, self.addr )
        try:
            super( server_thread, self ).run()
        except Exception as exc:
            log.warning("%s.echo service failure: %r\n%s", __package__, exc, traceback.format_exc() )
        log.info("%s.echo service PID [%5d/%5d] stopping on %r",
                 __package__, os.getpid(), self.ident, self.addr )

    def join( self ):
        try:
            self.conn.shutdown( socket.SHUT_WR )
        except:
            pass
        result			= super( server_thread, self ).join()
        if not self.is_alive():
            log.info("%s.echo service PID [%5d/%5d] complete on %r", 
                     __package__, os.getpid(), self.ident, self.addr )


def main():
    sock			= socket.socket( socket.AF_INET, socket.SOCK_STREAM )
    sock.setsockopt( socket.SOL_SOCKET, socket.SO_REUSEADDR, 1 ) # Avoid delay on next bind due to TIME_WAIT
    sock.bind( address )
    sock.listen( 100 ) # How may simultaneous unaccepted connection requests

    threads			= {}
    log.info("%s.echo service PID [%5d] running on %r", __package__, os.getpid(), address )
    done			= False
    while not done:
        try:
            acceptable		= accept( sock, timeout=.1 )
            if acceptable:
                conn, addr	= acceptable
                threads[addr]	= server_thread( target=echo_server, args=(conn, addr) )
                threads[addr].start()
        except KeyboardInterrupt as exc:
            log.warning("%s.echo service termination: %r", __package__, exc )
            done		= True
        except Exception as exc:
            log.warning("%s.echo service failure: %r\n%s", __package__, exc, traceback.format_exc() )
            done		= True
        finally:
            for addr in list( threads ):
                if done or not threads[addr].is_alive():
                    threads[addr].join()
                    del threads[addr]

    sock.close()
    log.info("%s.echo service PID [%5d] shutting down", __package__, os.getpid() )
    return 0

if __name__ == "__main__":
    sys.exit(main())